from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import ModuleType
from typing import Any


DEPENDENCY_FIELDS = ("DEPEND", "RDEPEND", "BDEPEND", "IDEPEND", "PDEPEND")
ANALYZER_RELATIVE = Path(
    "gentoo-overlay-development/scripts/dependency_analyzer.py")


class DependencyMetadataError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _installation_state_path() -> Path:
    home = Path.home()
    data_home = Path(os.environ.get(
        "XDG_DATA_HOME", home / ".local" / "share")).expanduser()
    return data_home / "gentoo-zh-skills" / "skill-installations.json"


def _analyzer_candidates() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("GZH_DEPENDENCY_ANALYZER")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    source_root = Path(__file__).resolve().parents[2]
    candidates.append(source_root / ".agents" / "skills" / ANALYZER_RELATIVE)
    state_path = _installation_state_path()
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        for target in state.get("targets", []) if isinstance(state, dict) else []:
            if isinstance(target, dict) and isinstance(target.get("target"), str):
                candidates.append(Path(target["target"]) / ANALYZER_RELATIVE)
    result: list[Path] = []
    for candidate in candidates:
        candidate = candidate.resolve(strict=False)
        if candidate.is_file() and candidate not in result:
            result.append(candidate)
    return result


def locate_dependency_analyzer() -> Path:
    candidates = _analyzer_candidates()
    if not candidates:
        raise DependencyMetadataError(
            "dependency analyzer is not installed; install the skill bundle or set "
            "GZH_DEPENDENCY_ANALYZER")
    hashes = {_sha256(path) for path in candidates}
    if len(hashes) != 1:
        paths = ", ".join(str(path) for path in candidates)
        raise DependencyMetadataError(
            f"multiple dependency analyzer revisions are installed: {paths}")
    return candidates[0]


def _load_analyzer(path: Path | None = None) -> ModuleType:
    path = Path(path or locate_dependency_analyzer())
    spec = importlib.util.spec_from_file_location("gzh_dependency_analyzer", path)
    if spec is None or spec.loader is None:
        raise DependencyMetadataError(f"cannot load dependency analyzer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_cache(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise DependencyMetadataError(f"cannot read metadata cache: {exc}") from exc
    for line in lines:
        if "=" not in line:
            raise DependencyMetadataError(f"invalid metadata cache line in {path}")
        key, value = line.split("=", 1)
        values[key] = value
    return values


def cached_ebuild_metadata(ebuild: Path) -> tuple[dict, dict]:
    ebuild = Path(ebuild).resolve()
    if not ebuild.is_file() or ebuild.suffix != ".ebuild" or len(ebuild.parents) < 3:
        raise DependencyMetadataError(f"not an ebuild path: {ebuild}")
    package_dir = ebuild.parent
    category_dir = package_dir.parent
    repository = category_dir.parent
    package = package_dir.name
    if not ebuild.name.startswith(f"{package}-"):
        raise DependencyMetadataError("ebuild filename does not match its package directory")
    cache = repository / "metadata" / "md5-cache" / category_dir.name / ebuild.stem
    if not cache.is_file():
        raise DependencyMetadataError(
            f"verified metadata cache is missing: {cache}; generate reviewed metadata "
            "with the repository's official procedure before dependency analysis")
    values = _parse_cache(cache)
    observed_md5 = hashlib.md5(ebuild.read_bytes(), usedforsecurity=False).hexdigest()
    if values.get("_md5_") != observed_md5:
        raise DependencyMetadataError(
            f"metadata cache does not match the ebuild: {cache}")
    if not values.get("EAPI"):
        raise DependencyMetadataError(f"metadata cache has no EAPI: {cache}")
    document = {
        "eapi": values["EAPI"],
        "dependencies": {field: values.get(field, "") for field in DEPENDENCY_FIELDS},
    }
    provenance = {
        "bytes": cache.stat().st_size,
        "cache": str(cache),
        "cache_sha256": _sha256(cache),
        "ebuild": str(ebuild),
        "ebuild_md5": observed_md5,
        "kind": "verified-md5-cache",
        "source": str(cache),
    }
    return document, provenance


def _provider_visibility(report: dict, api: Any) -> dict:
    results = []
    seen: set[str] = set()
    for record in report.get("atoms", []):
        atom = record.get("atom")
        if not isinstance(atom, str) or atom in seen:
            continue
        seen.add(atom)
        query = atom.lstrip("!")
        try:
            matches = [str(item) for item in api.db[api.root]["porttree"].dbapi.match(query)]
        except Exception as exc:
            results.append({"atom": atom, "error": str(exc), "matches": None})
        else:
            results.append({"atom": atom, "error": None, "matches": matches})
    settings = api.settings
    return {
        "arch": settings.get("ARCH"),
        "complete": all(item["error"] is None for item in results),
        "configured_repositories": list(settings.repositories.prepos_order),
        "profile_path": settings.get("PROFILE_PATH"),
        "results": results,
        "scope": "configured Portage repository set and active visibility settings",
    }


def analyze_ebuild_dependencies(
    ebuild: Path,
    *,
    use: list[str] | None = None,
    resolve_providers: bool = False,
    analyzer: ModuleType | None = None,
    portage_api: Any | None = None,
) -> dict:
    document, provenance = cached_ebuild_metadata(ebuild)
    if use is not None:
        document["use"] = use
    analyzer = analyzer or _load_analyzer()
    try:
        report = analyzer.analyze(document, provenance=provenance)
    except analyzer.AnalysisError as exc:
        return analyzer.error_report(exc, provenance)
    report["metadata"] = provenance
    report["provider_visibility"] = {
        "complete": False,
        "reason": "not requested",
        "results": [],
    }
    if resolve_providers:
        if portage_api is None:
            import portage as portage_api
        visibility = _provider_visibility(report, portage_api)
        report["provider_visibility"] = visibility
        if not visibility["complete"]:
            report["complete"] = False
            report["ok"] = False
    return report
