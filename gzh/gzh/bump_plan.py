from __future__ import annotations

import hashlib
from functools import cmp_to_key
from pathlib import Path

from portage.versions import vercmp, ververify

from gzh.bump import highest_ebuild, resolve_package_directory
from gzh.ebuild_parser import is_live, pv_from_name


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


def build_bump_plan(root: Path, cat_pkg: str, new_version: str) -> dict:
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
    return {
        "actions": [{
            "operation": "copy-ebuild",
            "source": str(source),
            "source_sha256": _sha256(source),
            "target": str(target),
        }],
        "can_apply": True,
        "category": category,
        "complete": True,
        "current_version": current_version,
        "manifest": {
            "exists": manifest.is_file(),
            "path": str(manifest),
            "sha256": _sha256(manifest) if manifest.is_file() else None,
        },
        "new_version": new_version,
        "ok": True,
        "package": package,
        "package_directory": str(pkg_dir),
        "retention": {
            "decision": "review-required",
            "reason": "version removal depends on repository policy and compatibility evidence",
        },
        "truncated": False,
        "versions": versions,
    }
