from __future__ import annotations

import hashlib
import json
import re
from functools import cmp_to_key
from pathlib import Path

from portage.versions import vercmp, ververify

from gzh.bump import highest_ebuild, resolve_package_directory
from gzh.ebuild_parser import is_live, pv_from_name


PACKAGE_MODELS = ("source", "prebuilt")

_BINARY_CONTAINER_RE = re.compile(
    r"(?i)\.(?:appimage|deb|rpm|apk|whl|egg|exe|msi|dmg|pkg|snap|flatpak)"
    r"(?=[?&#\"'\s)}\]]|$)")
_JVM_BYTECODE_RE = re.compile(
    r"(?i)\.(?:jar|war|ear)(?=[?&#\"'\s)}\]]|$)")
_STANDALONE_BUNDLE_RE = re.compile(
    r"(?i)\.(?:run|bin|sh)(?=[?&#\"'\s)}\]]|$)")
_ARCHIVE_RE = r"(?:tar(?:\.(?:gz|bz2|xz|zst))?|t(?:gz|bz2|xz)|zip|7z)"
_ARCH_RE = (
    r"(?:amd64|x86_64|x64|aarch64|arm64|armv7|i[3-6]86|x86|"
    r"riscv64|ppc64le|ppc64|s390x)"
)
_ARCHIVE_WITH_ARCH_RE = re.compile(
    rf"(?i)(?:{_ARCH_RE}[^\"'\s]{{0,96}}\.{_ARCHIVE_RE}|"
    rf"[^\"'\s]{{0,96}}\.{_ARCHIVE_RE}[^\"'\s]{{0,96}}{_ARCH_RE})"
    r"(?=[?&#\"'\s)}\]]|$)")
_SRC_URI_RE = re.compile(
    r"\bSRC_URI\s*\+?=\s*([\"'])(.*?)\1", re.DOTALL)
_SRC_URI_UNQUOTED_RE = re.compile(
    r"(?m)^\s*SRC_URI\s*\+?=\s*([^\"'\s#][^\s#]*)")
_VARIABLE_ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?:\"([^\"]*)\"|'([^']*)'|([^\s#]+))")
_VARIABLE_REFERENCE_RE = re.compile(
    r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def _src_uri_evidence(text: str) -> str:
    values = [
        *(match.group(2) for match in _SRC_URI_RE.finditer(text)),
        *(match.group(1) for match in _SRC_URI_UNQUOTED_RE.finditer(text)),
    ]
    assignments = {
        match.group(1): next(
            value for value in match.groups()[1:] if value is not None)
        for match in _VARIABLE_ASSIGNMENT_RE.finditer(text)
    }
    pending = list(values)
    seen = set()
    while pending:
        value = pending.pop()
        for reference in _VARIABLE_REFERENCE_RE.finditer(value):
            name = reference.group(1) or reference.group(2)
            if name in seen or name not in assignments:
                continue
            seen.add(name)
            values.append(assignments[name])
            pending.append(assignments[name])
    return "\n".join(values)


def _prebuilt_indicators(path: Path, package: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    scan_text = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))
    src_uri = _src_uri_evidence(scan_text)
    indicators = []
    if package.endswith("-bin"):
        indicators.append("package-name:-bin")
    if re.search(r"(?m)^\s*QA_PREBUILT\s*\+?=", scan_text):
        indicators.append("ebuild-variable:QA_PREBUILT")
    if re.search(r"(?m)^\s*inherit\s+[^#\n]*\brpm\b", scan_text):
        indicators.append("ebuild-eclass:rpm")
    for pattern, label, content in (
            (_BINARY_CONTAINER_RE, "src-uri:binary-container", src_uri),
            (_JVM_BYTECODE_RE, "src-uri:jvm-bytecode", src_uri),
            (_STANDALONE_BUNDLE_RE, "src-uri:standalone-bundle", src_uri),
            (_ARCHIVE_WITH_ARCH_RE,
             "src-uri:architecture-specific-archive", src_uri)):
        if pattern.search(content):
            indicators.append(label)
    return indicators


def _package_model_review(
        requested: str, indicators: list[str], *,
        assets_evidence: Path | None,
) -> dict:
    if requested not in PACKAGE_MODELS:
        choices = ", ".join(PACKAGE_MODELS)
        raise ValueError(f"package_model must be one of: {choices}")
    if requested == "source" and assets_evidence is not None:
        raise ValueError("assets_evidence applies only to the prebuilt package model")
    if requested == "source" and indicators:
        return {
            "complete": False,
            "requested": requested,
            "effective": None,
            "state": "conflict",
            "detected_prebuilt_indicators": indicators,
            "error": (
                "source package model conflicts with deterministic prebuilt indicators; "
                "review the payload and use package_model=prebuilt"),
        }
    return {
        "complete": True,
        "requested": requested,
        "effective": requested,
        "state": "classified",
        "detected_prebuilt_indicators": indicators,
        "error": None,
    }


def _release(value: object, label: str, version: str) -> dict:
    if not isinstance(value, dict) or set(value) != {
            "version", "release_url", "complete", "assets"}:
        raise ValueError(
            f"{label} release requires version, release_url, complete, and assets")
    if value["version"] != version:
        raise ValueError(f"{label} release version must be {version}")
    if not isinstance(value["release_url"], str) or not value["release_url"]:
        raise ValueError(f"{label} release_url must be a non-empty string")
    if value["complete"] is not True or not isinstance(value["assets"], list):
        raise ValueError(f"{label} release asset inventory must be complete")
    assets = []
    names = set()
    for item in value["assets"]:
        if (not isinstance(item, dict)
                or set(item) != {"filename", "architecture"}
                or not isinstance(item["filename"], str)
                or Path(item["filename"]).name != item["filename"]
                or not isinstance(item["architecture"], str)
                or not item["architecture"]):
            raise ValueError(f"{label} release contains an invalid asset record")
        if item["filename"] in names:
            raise ValueError(f"{label} release contains a duplicate asset filename")
        names.add(item["filename"])
        assets.append(dict(item))
    return {**value, "assets": sorted(
        assets, key=lambda item: (item["architecture"], item["filename"]))}


def _release_asset_review(
        path: Path, previous_version: str, new_version: str,
) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read release asset evidence: {exc}") from exc
    if not isinstance(document, dict) or set(document) != {
            "schema_version", "previous", "current", "decisions"}:
        raise ValueError(
            "release asset evidence requires schema_version, previous, current, "
            "and decisions")
    if document["schema_version"] != 1 or not isinstance(document["decisions"], dict):
        raise ValueError("release asset evidence schema version or decisions are invalid")
    previous = _release(document["previous"], "previous", previous_version)
    current = _release(document["current"], "current", new_version)

    def by_arch(release: dict) -> dict[str, set[str]]:
        result: dict[str, set[str]] = {}
        for item in release["assets"]:
            result.setdefault(item["architecture"], set()).add(item["filename"])
        return result

    old = by_arch(previous)
    new = by_arch(current)
    changes = []
    for architecture in sorted(new.keys() - old.keys()):
        changes.append({
            "id": f"architecture-added:{architecture}",
            "kind": "architecture-added", "architecture": architecture,
            "before": [], "after": sorted(new[architecture]),
        })
    for architecture in sorted(old.keys() - new.keys()):
        changes.append({
            "id": f"architecture-removed:{architecture}",
            "kind": "architecture-removed", "architecture": architecture,
            "before": sorted(old[architecture]), "after": [],
        })
    for architecture in sorted(old.keys() & new.keys()):
        if old[architecture] != new[architecture]:
            changes.append({
                "id": f"assets-changed:{architecture}",
                "kind": "assets-changed", "architecture": architecture,
                "before": sorted(old[architecture]),
                "after": sorted(new[architecture]),
            })
    known = {item["id"] for item in changes}
    unknown = sorted(set(document["decisions"]) - known)
    if unknown:
        raise ValueError(
            "release asset decisions reference unknown changes: " + ", ".join(unknown))
    decisions = []
    for change in changes:
        decision = document["decisions"].get(change["id"])
        decisions.append({
            **change,
            "decision": decision if isinstance(decision, str) and decision.strip() else None,
            "state": (
                "recorded" if isinstance(decision, str) and decision.strip()
                else "review-required"),
        })
    return {
        "complete": True,
        "evidence": str(Path(path).resolve()),
        "previous": previous,
        "current": current,
        "changes": decisions,
        "decisions_complete": all(item["state"] == "recorded" for item in decisions),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _versions(pkg_dir: Path, package: str) -> list[dict]:
    versions = []
    for path in pkg_dir.glob(f"{package}-*.ebuild"):
        version = pv_from_name(path.name)
        if not version or not ververify(version):
            continue
        versions.append({
            "live": is_live(version),
            "path": str(path),
            "sha256": _sha256(path),
            "version": version,
        })
    versions.sort(key=cmp_to_key(lambda a, b: vercmp(a["version"], b["version"])))
    return versions


def build_bump_plan(
        root: Path, cat_pkg: str, new_version: str, *,
        package_model: str,
        assets_evidence: Path | None = None,
) -> dict:
    category, package, pkg_dir = resolve_package_directory(root, cat_pkg)
    if not ververify(new_version) or is_live(new_version):
        raise ValueError(f"invalid released Gentoo version: {new_version}")
    source = highest_ebuild(pkg_dir, package)
    if source is None:
        raise FileNotFoundError(
            f"no released ebuild for {package} in {pkg_dir} "
            "(live-only or empty); no safe bump plan can be created")
    current_version = pv_from_name(source.name)
    if vercmp(new_version, current_version) <= 0:
        raise ValueError(
            f"new version {new_version} must be greater than current version {current_version}")
    target = pkg_dir / f"{package}-{new_version}.ebuild"
    if target.exists():
        raise FileExistsError(f"target already exists: {target}")
    versions = _versions(pkg_dir, package)
    manifest = pkg_dir / "Manifest"
    indicators = _prebuilt_indicators(source, package)
    package_model_review = _package_model_review(
        package_model, indicators, assets_evidence=assets_evidence)
    prebuilt = package_model == "prebuilt" or bool(indicators)
    if prebuilt and assets_evidence is None:
        release_assets = {
            "complete": False,
            "required": True,
            "error": "prebuilt bump requires complete previous/current release assets",
            "changes": [],
            "decisions_complete": False,
        }
    elif prebuilt:
        release_assets = {
            "required": True,
            **_release_asset_review(
                assets_evidence, current_version, new_version),
        }
    else:
        release_assets = {
            "complete": True,
            "required": False,
            "changes": [],
            "decisions_complete": True,
        }
    can_apply = (
        package_model_review["complete"]
        and release_assets["complete"]
        and release_assets["decisions_complete"])
    return {
        "actions": [{
            "operation": "copy-ebuild",
            "source": str(source),
            "source_sha256": _sha256(source),
            "target": str(target),
        }],
        "can_apply": can_apply,
        "category": category,
        "complete": package_model_review["complete"] and release_assets["complete"],
        "current_version": current_version,
        "manifest": {
            "exists": manifest.is_file(),
            "path": str(manifest),
            "sha256": _sha256(manifest) if manifest.is_file() else None,
        },
        "new_version": new_version,
        "ok": can_apply,
        "package": package,
        "package_directory": str(pkg_dir),
        "package_model": package_model_review,
        "retention": {
            "decision": "review-required",
            "reason": "version removal depends on repository policy and compatibility evidence",
        },
        "release_assets": release_assets,
        "truncated": False,
        "versions": versions,
    }
