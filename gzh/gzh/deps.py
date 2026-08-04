from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
from types import ModuleType
from typing import Any


DEPENDENCY_FIELDS = ("DEPEND", "RDEPEND", "BDEPEND", "IDEPEND", "PDEPEND")
ANALYZER_RELATIVE = Path(
    "gentoo-overlay-development/scripts/dependency_analyzer.py")
COMPARISON_SCHEMA_VERSION = 1
COMPARISON_TOOL = "gzh-dependency-comparison"
MAX_METADATA_BYTES = 1024 * 1024


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


def _read_regular_file(path: Path, label: str) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DependencyMetadataError(f"cannot inspect {label}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise DependencyMetadataError(f"{label} is not a regular file: {path}")
    if info.st_size > MAX_METADATA_BYTES:
        raise DependencyMetadataError(
            f"{label} exceeds {MAX_METADATA_BYTES} bytes: {path}")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise DependencyMetadataError(
                    f"{label} is not a regular file: {path}")
            content = handle.read(MAX_METADATA_BYTES + 1)
    except DependencyMetadataError:
        raise
    except OSError as exc:
        raise DependencyMetadataError(f"cannot read {label}: {exc}") from exc
    if len(content) > MAX_METADATA_BYTES:
        raise DependencyMetadataError(
            f"{label} exceeds {MAX_METADATA_BYTES} bytes: {path}")
    return content


def _parse_cache(path: Path) -> tuple[dict[str, str], bytes]:
    values: dict[str, str] = {}
    try:
        content = _read_regular_file(path, "metadata cache")
        lines = content.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DependencyMetadataError(f"cannot read metadata cache: {exc}") from exc
    for line in lines:
        if "=" not in line:
            raise DependencyMetadataError(f"invalid metadata cache line in {path}")
        key, value = line.split("=", 1)
        if not key or key in values:
            raise DependencyMetadataError(f"invalid metadata cache key in {path}")
        values[key] = value
    return values, content


def cached_ebuild_metadata(ebuild: Path) -> tuple[dict, dict]:
    requested_ebuild = Path(ebuild).expanduser()
    if requested_ebuild.is_symlink():
        raise DependencyMetadataError(
            f"ebuild is not a regular file: {requested_ebuild}")
    ebuild = requested_ebuild.resolve()
    if not ebuild.is_file() or ebuild.suffix != ".ebuild" or len(ebuild.parents) < 3:
        raise DependencyMetadataError(f"not an ebuild path: {ebuild}")
    package_dir = ebuild.parent
    category_dir = package_dir.parent
    repository = category_dir.parent
    package = package_dir.name
    if not ebuild.name.startswith(f"{package}-"):
        raise DependencyMetadataError("ebuild filename does not match its package directory")
    cache = repository / "metadata" / "md5-cache" / category_dir.name / ebuild.stem
    if not cache.exists() and not cache.is_symlink():
        raise DependencyMetadataError(
            f"verified metadata cache is missing: {cache}; generate reviewed metadata "
            "with the repository's official procedure before dependency analysis")
    ebuild_content = _read_regular_file(ebuild, "ebuild")
    values, cache_content = _parse_cache(cache)
    observed_md5 = hashlib.md5(
        ebuild_content, usedforsecurity=False).hexdigest()
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
        "bytes": len(cache_content),
        "cache": str(cache),
        "cache_sha256": hashlib.sha256(cache_content).hexdigest(),
        "ebuild": str(ebuild),
        "ebuild_bytes": len(ebuild_content),
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


def _analyze_dependency_document(
    document: dict[str, Any],
    provenance: dict[str, Any],
    *,
    use: list[str] | None,
    analyzer: ModuleType,
) -> dict[str, Any]:
    request = {
        "eapi": document["eapi"],
        "dependencies": dict(document["dependencies"]),
    }
    if use is not None:
        request["use"] = list(use)
    try:
        report = analyzer.analyze(request, provenance=provenance)
    except analyzer.AnalysisError as exc:
        report = analyzer.error_report(exc, provenance)
    report["metadata"] = provenance
    report["provider_visibility"] = {
        "complete": False,
        "reason": "not requested",
        "results": [],
    }
    return report


def analyze_ebuild_dependencies(
    ebuild: Path,
    *,
    use: list[str] | None = None,
    resolve_providers: bool = False,
    analyzer: ModuleType | None = None,
    portage_api: Any | None = None,
) -> dict:
    document, provenance = cached_ebuild_metadata(ebuild)
    analyzer = analyzer or _load_analyzer()
    report = _analyze_dependency_document(
        document, provenance, use=use, analyzer=analyzer)
    if resolve_providers:
        if portage_api is None:
            import portage as portage_api
        visibility = _provider_visibility(report, portage_api)
        report["provider_visibility"] = visibility
        if not visibility["complete"]:
            report["complete"] = False
            report["ok"] = False
    return report


def _comparison_use(use: list[str] | None) -> dict[str, Any]:
    return {
        "provided": use is not None,
        "requested": None if use is None else list(use),
        "scope": "one shared USE state for both inputs",
    }


def _comparison_error(
    *,
    inputs: dict[str, dict[str, Any]],
    use: list[str] | None,
    errors: list[dict[str, Any]],
    analyses: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "tool": COMPARISON_TOOL,
        "ok": False,
        "complete": False,
        "state": "error",
        "changed": None,
        "modes": {
            "declarations": "potential",
            "use_selection": "reduced" if use is not None else "not-requested",
        },
        "use": _comparison_use(use),
        "inputs": inputs,
        "analyses": analyses or {},
        "errors": errors,
        "fields": None,
    }


def _analysis_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        key: report.get(key)
        for key in (
            "complete", "ok", "truncated", "error", "engine", "eapi", "use",
            "selection",
        )
        if key in report
    }


def _validate_comparison_analysis(
    side: str,
    report: Any,
    *,
    explicit_use: bool,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    if not isinstance(report, dict):
        return [{
            "side": side,
            "stage": "analysis",
            "code": "invalid-analysis-schema",
            "detail": "analyzer report is not an object",
        }]
    if (report.get("ok") is not True or report.get("complete") is not True
            or report.get("truncated") is not False):
        errors.append({
            "side": side,
            "stage": "analysis",
            "code": "analysis-not-complete",
            "detail": report.get("error"),
        })
        return errors

    expected_selection = "reduced" if explicit_use else "potential"
    observed_use = report.get("use")
    fields = report.get("fields")
    records = report.get("atoms")
    metadata = report.get("metadata")
    if (report.get("selection") != expected_selection
            or not isinstance(observed_use, dict)
            or observed_use.get("provided") is not explicit_use
            or not isinstance(observed_use.get("enabled"), list)
            or not all(isinstance(flag, str) for flag in observed_use["enabled"])
            or not isinstance(observed_use.get("disabled"), list)
            or not all(isinstance(flag, str) for flag in observed_use["disabled"])
            or set(observed_use["enabled"]) & set(observed_use["disabled"])
            or not isinstance(fields, dict)
            or not isinstance(records, list)
            or not isinstance(metadata, dict)
            or metadata.get("kind") != "verified-md5-cache"):
        errors.append({
            "side": side,
            "stage": "analysis",
            "code": "invalid-analysis-schema",
            "detail": "analyzer selection, USE state, fields, or atoms are invalid",
        })
        return errors

    records_by_field: dict[str, list[str]] = defaultdict(list)
    for record in records:
        provenance = record.get("provenance") if isinstance(record, dict) else None
        field = provenance.get("field") if isinstance(provenance, dict) else None
        required = (
            "atom", "cp", "blocker", "slot", "sub_slot", "slot_operator",
            "slot_operator_built",
        )
        if (not isinstance(record, dict)
                or field not in DEPENDENCY_FIELDS
                or any(key not in record for key in required)
                or not isinstance(record.get("atom"), str)
                or not isinstance(record.get("cp"), str)):
            errors.append({
                "side": side,
                "stage": "analysis",
                "code": "invalid-analysis-schema",
                "detail": "analyzer returned an invalid atom record",
            })
            return errors
        records_by_field[field].append(record["atom"])

    for field in DEPENDENCY_FIELDS:
        value = fields.get(field)
        if (not isinstance(value, dict)
                or not isinstance(value.get("atoms"), list)
                or not all(isinstance(atom, str) for atom in value["atoms"])
                or not isinstance(value.get("conditional_flags"), list)
                or not all(isinstance(flag, str) for flag in value["conditional_flags"])
                or not isinstance(value.get("raw"), str)
                or "parsed_potential" not in value
                or Counter(value["atoms"]) != Counter(records_by_field[field])):
            errors.append({
                "side": side,
                "stage": "analysis",
                "code": "invalid-analysis-schema",
                "detail": f"analyzer returned an invalid {field} record",
            })
            return errors
    return errors


def _field_records(report: dict[str, Any], field: str) -> list[dict[str, Any]]:
    return [
        record for record in report["atoms"]
        if record["provenance"]["field"] == field
    ]


def _atom_delta(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    before_count = Counter(before)
    after_count = Counter(after)
    added = sorted((after_count - before_count).elements())
    removed = sorted((before_count - after_count).elements())
    return added, removed


def _record_summary(record: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {"atom": record["atom"], **{key: record[key] for key in keys}}


def _change_candidates(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    *,
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    before_by_cp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    after_by_cp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in before:
        before_by_cp[record["cp"]].append(record)
    for record in after:
        after_by_cp[record["cp"]].append(record)

    changes = []
    for cp in sorted(set(before_by_cp) & set(after_by_cp)):
        before_values = {tuple(record[key] for key in keys) for record in before_by_cp[cp]}
        after_values = {tuple(record[key] for key in keys) for record in after_by_cp[cp]}
        if before_values == after_values:
            continue
        before_summaries = [
            _record_summary(record, keys) for record in before_by_cp[cp]
        ]
        after_summaries = [
            _record_summary(record, keys) for record in after_by_cp[cp]
        ]
        changes.append({
            "cp": cp,
            "before": sorted(before_summaries, key=lambda value: json.dumps(
                value, sort_keys=True, separators=(",", ":"))),
            "after": sorted(after_summaries, key=lambda value: json.dumps(
                value, sort_keys=True, separators=(",", ":"))),
        })
    return changes


def _normalized_expression(value: str) -> str:
    return " ".join(value.split())


def _compare_field(
    before_report: dict[str, Any],
    after_report: dict[str, Any],
    field: str,
    *,
    before_reduced: dict[str, Any] | None,
    after_reduced: dict[str, Any] | None,
) -> dict[str, Any]:
    before_field = before_report["fields"][field]
    after_field = after_report["fields"][field]
    before_records = _field_records(before_report, field)
    after_records = _field_records(after_report, field)
    added, removed = _atom_delta(before_field["atoms"], after_field["atoms"])
    reduced_delta = None
    if before_reduced is not None and after_reduced is not None:
        reduced_added, reduced_removed = _atom_delta(
            before_reduced["fields"][field]["atoms"],
            after_reduced["fields"][field]["atoms"],
        )
        reduced_delta = {
            "added_atoms": reduced_added,
            "removed_atoms": reduced_removed,
        }

    before_flags = set(before_field["conditional_flags"])
    after_flags = set(after_field["conditional_flags"])
    raw_changed = (
        _normalized_expression(before_field["raw"])
        != _normalized_expression(after_field["raw"])
    )
    potential_structure_changed = (
        before_field["parsed_potential"] != after_field["parsed_potential"]
    )
    flags_added = sorted(after_flags - before_flags)
    flags_removed = sorted(before_flags - after_flags)
    expression_candidate = raw_changed and not potential_structure_changed
    condition_candidate = bool(flags_added or flags_removed or expression_candidate)
    condition_changes = {
        "candidate": condition_candidate,
        "flags_added": flags_added,
        "flags_removed": flags_removed,
        "before_expression": before_field["raw"] if condition_candidate else None,
        "after_expression": after_field["raw"] if condition_candidate else None,
        "selected_atoms_added": (
            reduced_delta["added_atoms"]
            if reduced_delta is not None and expression_candidate else None
        ),
        "selected_atoms_removed": (
            reduced_delta["removed_atoms"]
            if reduced_delta is not None and expression_candidate else None
        ),
    }
    slot_changes = _change_candidates(
        before_records,
        after_records,
        keys=("slot", "sub_slot", "slot_operator", "slot_operator_built"),
    )
    blocker_changes = _change_candidates(
        before_records,
        after_records,
        keys=("blocker",),
    )
    changed = bool(
        added or removed or raw_changed or potential_structure_changed
        or condition_changes["flags_added"] or condition_changes["flags_removed"]
        or slot_changes or blocker_changes
    )
    return {
        "changed": changed,
        "added_atoms": added,
        "removed_atoms": removed,
        "use_reduced_delta": reduced_delta,
        "raw_expression_changed": raw_changed,
        "raw_expression": ({
            "before": before_field["raw"],
            "after": after_field["raw"],
        } if raw_changed else None),
        "potential_structure_changed": potential_structure_changed,
        "potential_structure": ({
            "before": before_field["parsed_potential"],
            "after": after_field["parsed_potential"],
        } if potential_structure_changed else None),
        "condition_changes": condition_changes,
        "slot_change_candidates": slot_changes,
        "blocker_change_candidates": blocker_changes,
    }


def compare_ebuild_dependencies(
    before_ebuild: Path,
    after_ebuild: Path,
    *,
    use: list[str] | None = None,
    analyzer: ModuleType | None = None,
) -> dict[str, Any]:
    """Compare two verified cache snapshots using one Portage analyzer revision."""
    requested = {
        "before": {"ebuild": str(Path(before_ebuild).resolve()), "metadata": None},
        "after": {"ebuild": str(Path(after_ebuild).resolve()), "metadata": None},
    }
    try:
        analyzer = analyzer or _load_analyzer()
    except Exception as exc:
        return _comparison_error(
            inputs=requested,
            use=use,
            errors=[{
                "side": None,
                "stage": "analyzer-load",
                "code": "analyzer-unavailable",
                "detail": str(exc),
            }],
        )

    documents: dict[str, dict[str, Any]] = {}
    provenance: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for side, ebuild in (("before", before_ebuild), ("after", after_ebuild)):
        try:
            document, metadata = cached_ebuild_metadata(ebuild)
        except DependencyMetadataError as exc:
            errors.append({
                "side": side,
                "stage": "metadata",
                "code": "metadata-not-verified",
                "detail": str(exc),
            })
            continue
        documents[side] = document
        provenance[side] = metadata
        requested[side]["metadata"] = metadata

    if errors:
        return _comparison_error(inputs=requested, use=use, errors=errors)

    potential: dict[str, dict[str, Any]] = {}
    reduced: dict[str, dict[str, Any]] = {}
    for side in ("before", "after"):
        try:
            potential_report = _analyze_dependency_document(
                documents[side], provenance[side], use=None, analyzer=analyzer)
            reduced_report = (
                _analyze_dependency_document(
                    documents[side], provenance[side], use=list(use), analyzer=analyzer)
                if use is not None else None
            )
        except Exception as exc:
            errors.append({
                "side": side,
                "stage": "analysis",
                "code": "analysis-exception",
                "detail": f"{type(exc).__name__}: {exc}",
            })
            continue
        potential[side] = potential_report
        errors.extend(_validate_comparison_analysis(
            side, potential_report, explicit_use=False))
        if reduced_report is not None:
            reduced[side] = reduced_report
            reduced_errors = _validate_comparison_analysis(
                side, reduced_report, explicit_use=True)
            for error in reduced_errors:
                error["mode"] = "reduced"
            errors.extend(reduced_errors)

    analyses = {
        side: {
            "potential": _analysis_summary(potential[side]) if side in potential else None,
            "reduced": _analysis_summary(reduced[side]) if side in reduced else None,
        }
        for side in ("before", "after")
    }
    selected = reduced if use is not None else potential
    if not errors and selected["before"]["use"].get("enabled") \
            != selected["after"]["use"].get("enabled"):
        errors.append({
            "side": None,
            "stage": "comparison",
            "code": "use-state-mismatch",
            "detail": "analyzer normalized different enabled USE states",
        })
    if not errors and selected["before"]["use"].get("disabled") \
            != selected["after"]["use"].get("disabled"):
        errors.append({
            "side": None,
            "stage": "comparison",
            "code": "use-state-mismatch",
            "detail": "analyzer normalized different disabled USE states",
        })
    if errors:
        return _comparison_error(
            inputs=requested,
            use=use,
            errors=errors,
            analyses=analyses,
        )

    before_report = potential["before"]
    after_report = potential["after"]
    fields = {
        field: _compare_field(
            before_report,
            after_report,
            field,
            before_reduced=reduced.get("before"),
            after_reduced=reduced.get("after"),
        )
        for field in DEPENDENCY_FIELDS
    }
    eapi = {
        "before": before_report["eapi"],
        "after": after_report["eapi"],
        "changed": before_report["eapi"] != after_report["eapi"],
    }
    normalized_use = _comparison_use(use)
    normalized_use["before"] = selected["before"]["use"]
    normalized_use["after"] = selected["after"]["use"]
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "tool": COMPARISON_TOOL,
        "ok": True,
        "complete": True,
        "state": "complete",
        "changed": eapi["changed"] or any(value["changed"] for value in fields.values()),
        "modes": {
            "declarations": "potential",
            "use_selection": "reduced" if use is not None else "not-requested",
        },
        "use": normalized_use,
        "inputs": requested,
        "analyses": analyses,
        "errors": [],
        "eapi": eapi,
        "fields": fields,
        "limitations": [
            "Declaration atom deltas always compare Portage-parsed potential trees; an explicit complete shared USE state adds a separate reduced-selection delta.",
            "Condition, slot, and blocker changes are review candidates; they do not establish dependency correctness, provider compatibility, ABI behavior, or rebuild requirements.",
            "A condition candidate requires changed normalized cache syntax with the same Portage-parsed potential structure; logically equivalent expressions are not proven equivalent.",
            "No ebuild or eclass is sourced, and no package provider or reverse dependency is resolved.",
        ],
    }
