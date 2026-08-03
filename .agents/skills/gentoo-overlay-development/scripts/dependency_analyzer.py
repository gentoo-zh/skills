#!/usr/bin/env python3
"""Analyze extracted Gentoo ebuild dependency metadata without executing it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any


SCHEMA_VERSION = 1
TOOL_NAME = "gentoo-overlay-dependency-analyzer"
MAX_INPUT_BYTES = 1024 * 1024
DEPENDENCY_FIELDS = ("DEPEND", "RDEPEND", "BDEPEND", "IDEPEND", "PDEPEND")


class AnalysisError(Exception):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.input_provenance: dict[str, Any] | None = None


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise AnalysisError("invalid_cli", message)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")


def input_provenance(data: bytes, kind: str, source: str) -> dict[str, Any]:
    return {
        "bytes": len(data),
        "kind": kind,
        "sha256": hashlib.sha256(data).hexdigest(),
        "source": source,
    }


def atomic_write(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_portage() -> SimpleNamespace:
    try:
        import portage
        from portage.dep import Atom, use_reduce
        from portage.eapi import (
            eapi_has_bdepend,
            eapi_has_idepend,
            eapi_has_strong_blocks,
            eapi_is_supported,
        )
    except (ImportError, ModuleNotFoundError) as exc:
        raise AnalysisError(
            "portage_unavailable",
            "the Portage Python API is required for dependency analysis",
        ) from exc
    return SimpleNamespace(
        module=portage,
        Atom=Atom,
        use_reduce=use_reduce,
        eapi_is_supported=eapi_is_supported,
        eapi_has_bdepend=eapi_has_bdepend,
        eapi_has_idepend=eapi_has_idepend,
        eapi_has_strong_blocks=eapi_has_strong_blocks,
    )


def normalize_eapi(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise AnalysisError("invalid_input", "eapi must be a string or integer")
    eapi = str(value).strip()
    if not eapi:
        raise AnalysisError("invalid_input", "eapi must not be empty")
    return eapi


def normalize_flag_list(value: Any, name: str, *, signed: bool) -> tuple[set[str], set[str]]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AnalysisError("invalid_input", f"{name} must be an array of strings")
    enabled: set[str] = set()
    disabled: set[str] = set()
    for item in value:
        flag = item.strip()
        if not flag:
            raise AnalysisError("invalid_input", f"{name} contains an empty USE flag")
        if signed and flag[:1] in {"+", "-"}:
            target = disabled if flag[0] == "-" else enabled
            flag = flag[1:]
            if not flag:
                raise AnalysisError("invalid_input", f"{name} contains an empty USE flag")
        else:
            target = enabled
        target.add(flag)
    return enabled, disabled


def normalize_use(value: Any) -> tuple[bool, set[str], set[str]]:
    if value is None:
        return False, set(), set()
    if isinstance(value, list):
        enabled, disabled = normalize_flag_list(value, "use", signed=True)
    elif isinstance(value, dict):
        if set(value) <= {"enabled", "disabled"}:
            enabled, unexpected = normalize_flag_list(
                value.get("enabled", []), "use.enabled", signed=False)
            disabled_as_enabled, disabled = normalize_flag_list(
                value.get("disabled", []), "use.disabled", signed=False)
            if unexpected or disabled:
                raise AssertionError("unsigned USE flag parsing returned signed values")
            disabled = disabled_as_enabled
        elif all(isinstance(flag, str) and isinstance(state, bool)
                 for flag, state in value.items()):
            enabled = {flag for flag, state in value.items() if state}
            disabled = {flag for flag, state in value.items() if not state}
        else:
            raise AnalysisError(
                "invalid_input",
                "use must map enabled/disabled arrays or USE flags to booleans",
            )
    else:
        raise AnalysisError("invalid_input", "use must be an array or object")
    overlap = enabled & disabled
    if overlap:
        raise AnalysisError(
            "invalid_use_state",
            "USE flags cannot be both enabled and disabled",
            flags=sorted(overlap),
        )
    return True, enabled, disabled


def normalize_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise AnalysisError("invalid_input", "JSON input must be an object")
    allowed = {"eapi", "use", "dependencies", "fields", *DEPENDENCY_FIELDS}
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise AnalysisError("invalid_input", "JSON input contains unknown keys", keys=unknown)
    if "dependencies" in document and "fields" in document:
        raise AnalysisError(
            "invalid_input", "dependencies and fields cannot both be supplied")

    container_name = "dependencies" if "dependencies" in document else (
        "fields" if "fields" in document else None)
    container = document.get(container_name, {}) if container_name else {}
    if not isinstance(container, dict):
        raise AnalysisError("invalid_input", f"{container_name} must be an object")
    unknown_fields = sorted(set(container) - set(DEPENDENCY_FIELDS))
    if unknown_fields:
        raise AnalysisError(
            "invalid_input", f"{container_name} contains unknown fields",
            fields=unknown_fields)

    duplicates = sorted(set(container) & set(document))
    if duplicates:
        raise AnalysisError(
            "invalid_input", "dependency fields must be supplied in only one location",
            fields=duplicates)

    dependencies: dict[str, str] = {}
    provenance: dict[str, str | None] = {}
    for field in DEPENDENCY_FIELDS:
        if field in container:
            value = container[field]
            source = f"{container_name}.{field}"
        elif field in document:
            value = document[field]
            source = field
        else:
            value = ""
            source = None
        if not isinstance(value, str):
            raise AnalysisError("invalid_input", f"{source} must be a string")
        dependencies[field] = value
        provenance[field] = source

    if "eapi" not in document:
        raise AnalysisError("invalid_input", "eapi is required")
    use_provided, enabled, disabled = normalize_use(document.get("use"))
    return {
        "eapi": normalize_eapi(document["eapi"]),
        "dependencies": dependencies,
        "provenance": provenance,
        "use_provided": use_provided,
        "enabled": enabled,
        "disabled": disabled,
    }


def validate_use_flag(api: SimpleNamespace, flag: str, eapi: str) -> None:
    observed: set[str] = set()
    expression = f"{flag}? ( virtual/libc )"

    def record(candidate: str) -> bool:
        observed.add(candidate)
        return True

    try:
        api.use_reduce(
            expression, eapi=eapi, matchall=True, token_class=api.Atom)
        api.use_reduce(
            expression, eapi=eapi, matchall=True,
            token_class=api.Atom, is_valid_flag=record)
    except Exception as exc:
        raise AnalysisError(
            "invalid_use_state", f"invalid USE flag for EAPI {eapi}: {flag}",
            flag=flag, eapi=eapi) from exc
    if observed != {flag}:
        raise AnalysisError(
            "invalid_use_state", f"invalid USE flag for EAPI {eapi}: {flag}",
            flag=flag, eapi=eapi)


def parse_dependency(
    api: SimpleNamespace,
    expression: str,
    field: str,
    eapi: str,
    *,
    enabled: set[str] | None = None,
    record_flags: set[str] | None = None,
    matchall: bool = False,
) -> list[Any]:
    def valid_flag(flag: str) -> bool:
        if record_flags is not None:
            record_flags.add(flag)
        return True

    try:
        result = api.use_reduce(
            expression,
            uselist=() if enabled is None else enabled,
            eapi=eapi,
            opconvert=True,
            matchall=matchall,
            token_class=api.Atom,
        )
        if record_flags is not None:
            api.use_reduce(
                expression,
                uselist=() if enabled is None else enabled,
                eapi=eapi,
                opconvert=True,
                matchall=matchall,
                token_class=api.Atom,
                is_valid_flag=valid_flag,
            )
        return result
    except Exception as exc:
        raise AnalysisError(
            "invalid_dependency",
            f"{field} is invalid for EAPI {eapi}: {exc}",
            field=field,
            eapi=eapi,
        ) from exc


def walk_atoms(api: SimpleNamespace, tree: list[Any], *, in_any_of: bool = False):
    any_of = bool(tree and tree[0] == "||")
    for item in tree[1:] if any_of else tree:
        if isinstance(item, list):
            yield from walk_atoms(api, item, in_any_of=in_any_of or any_of)
        elif isinstance(item, api.Atom):
            yield item, in_any_of or any_of


def validate_context(api: SimpleNamespace, tree: list[Any], field: str, eapi: str) -> None:
    for atom, in_any_of in walk_atoms(api, tree):
        if atom.slot_operator_built:
            raise AnalysisError(
                "invalid_dependency",
                f"{field} contains a package-manager-expanded equals slot operator",
                field=field,
                eapi=eapi,
                atom=str(atom),
            )
        if atom.slot_operator != "=":
            continue
        if field == "PDEPEND" or in_any_of:
            location = "PDEPEND" if field == "PDEPEND" else "an any-of group"
            raise AnalysisError(
                "invalid_dependency",
                f"{field} uses the equals slot operator inside {location}",
                field=field,
                eapi=eapi,
                atom=str(atom),
            )


def serialize_tree(value: Any) -> Any:
    if isinstance(value, list):
        return [serialize_tree(item) for item in value]
    return str(value)


def atom_record(
    api: SimpleNamespace,
    atom: Any,
    field: str,
    index: int,
    source: str | None,
    eapi: str,
) -> dict[str, Any]:
    if atom.blocker:
        blocker = "strong" if atom.blocker.overlap.forbid else (
            "weak" if api.eapi_has_strong_blocks(eapi) else "unspecified")
    else:
        blocker = None
    use_dependencies = [] if atom.use is None else [str(token) for token in atom.use.tokens]
    return {
        "atom": str(atom),
        "blocker": blocker,
        "cp": str(atom.cp),
        "operator": atom.operator,
        "repository": atom.repo,
        "slot": atom.slot,
        "slot_operator": atom.slot_operator,
        "slot_operator_built": bool(atom.slot_operator_built),
        "sub_slot": atom.sub_slot,
        "use_dependencies": use_dependencies,
        "provenance": {
            "field": field,
            "occurrence": index,
            "source": source,
        },
    }


def inventory(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    blockers = []
    slot_operators = []
    repository_qualifiers = []
    occurrences: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        provenance = record["provenance"]
        occurrences.setdefault(record["atom"], []).append(provenance)
        if record["blocker"] is not None:
            blockers.append({
                "atom": record["atom"],
                "strength": record["blocker"],
                "provenance": provenance,
            })
        if record["slot_operator"] is not None:
            slot_operators.append({
                "atom": record["atom"],
                "operator": record["slot_operator"],
                "built": record["slot_operator_built"],
                "provenance": provenance,
            })
        if record["repository"] is not None:
            repository_qualifiers.append({
                "atom": record["atom"],
                "repository": record["repository"],
                "provenance": provenance,
            })

    duplicates = []
    for atom in sorted(occurrences):
        provenance = occurrences[atom]
        if len({item["field"] for item in provenance}) > 1:
            duplicates.append({"atom": atom, "provenance": provenance})
    return {
        "blockers": blockers,
        "slot_operators": slot_operators,
        "repository_qualifiers": repository_qualifiers,
        "cross_field_duplicates": duplicates,
    }


def analyze(
    document: Any,
    *,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if provenance is None:
        provenance = input_provenance(
            canonical_bytes(document), "structured", "python-api")
    request = normalize_document(document)
    api = load_portage()
    eapi = request["eapi"]
    if not api.eapi_is_supported(eapi):
        raise AnalysisError(
            "unsupported_eapi", f"Portage does not support EAPI {eapi}", eapi=eapi)
    if request["dependencies"]["BDEPEND"].strip() and not api.eapi_has_bdepend(eapi):
        raise AnalysisError(
            "unsupported_field", f"BDEPEND is not supported by EAPI {eapi}",
            field="BDEPEND", eapi=eapi)
    if request["dependencies"]["IDEPEND"].strip() and not api.eapi_has_idepend(eapi):
        raise AnalysisError(
            "unsupported_field", f"IDEPEND is not supported by EAPI {eapi}",
            field="IDEPEND", eapi=eapi)

    for flag in sorted(request["enabled"] | request["disabled"]):
        validate_use_flag(api, flag, eapi)

    potential: dict[str, list[Any]] = {}
    conditional_flags: dict[str, list[str]] = {}
    referenced_flags: set[str] = set()
    for field in DEPENDENCY_FIELDS:
        field_flags: set[str] = set()
        tree = parse_dependency(
            api, request["dependencies"][field], field, eapi,
            record_flags=field_flags, matchall=True)
        validate_context(api, tree, field, eapi)
        potential[field] = tree
        conditional_flags[field] = sorted(field_flags)
        referenced_flags.update(field_flags)

    if request["use_provided"]:
        missing = referenced_flags - request["enabled"] - request["disabled"]
        if missing:
            raise AnalysisError(
                "incomplete_use_state",
                "every referenced conditional USE flag needs an explicit state",
                flags=sorted(missing),
            )

    fields: dict[str, dict[str, Any]] = {}
    records: list[dict[str, Any]] = []
    for field in DEPENDENCY_FIELDS:
        if request["use_provided"]:
            selected = parse_dependency(
                api, request["dependencies"][field], field, eapi,
                enabled=request["enabled"])
            validate_context(api, selected, field, eapi)
            reduced = serialize_tree(selected)
        else:
            selected = potential[field]
            reduced = None
        field_records = []
        for index, (atom, _in_any_of) in enumerate(walk_atoms(api, selected)):
            record = atom_record(
                api, atom, field, index, request["provenance"][field], eapi)
            records.append(record)
            field_records.append(record["atom"])
        fields[field] = {
            "atoms": field_records,
            "conditional_flags": conditional_flags[field],
            "parsed_potential": serialize_tree(potential[field]),
            "provenance": request["provenance"][field],
            "raw": request["dependencies"][field],
            "reduced": reduced,
        }

    inventories = inventory(records)
    limitations = [
        "Input is treated as extracted metadata; no ebuild or eclass is sourced or executed.",
        "Only declared atoms are reported; packages, alternatives, providers, transitive dependencies, and runtime requirements are not resolved or inferred.",
        "Any-of groups are preserved and no provider choice is made.",
        "Cross-field duplicates compare exact reported atom strings for the selected mode and do not establish semantic redundancy.",
        "Equals slot operators are syntax-checked, but matching build-time DEPEND coverage is not resolved.",
        "IUSE_EFFECTIVE was not supplied, so conditional flag syntax is validated but membership is not.",
        "Repository-qualified atoms are invalid in supported ebuild EAPIs and are rejected by Portage.",
        "Legacy implicit RDEPEND=DEPEND behavior is not synthesized.",
    ]
    if not request["use_provided"]:
        limitations.append(
            "No explicit USE state was supplied; every conditional branch is reported as potential and reduced trees are null.")

    return {
        "complete": True,
        "generated_at": utc_now(),
        "input_provenance": provenance,
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": True,
        "truncated": False,
        "engine": {
            "name": "portage",
            "version": str(getattr(api.module, "VERSION", "unknown")),
        },
        "eapi": eapi,
        "use": {
            "provided": request["use_provided"],
            "enabled": sorted(request["enabled"]),
            "disabled": sorted(request["disabled"]),
            "referenced": sorted(referenced_flags),
        },
        "selection": "reduced" if request["use_provided"] else "potential",
        "fields": fields,
        "atoms": records,
        **inventories,
        "limitations": limitations,
    }


def error_report(
    error: AnalysisError,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detail = {"code": error.code, "message": error.message}
    if error.details:
        detail["details"] = error.details
    return {
        "complete": True,
        "generated_at": utc_now(),
        "input_provenance": provenance or error.input_provenance,
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "ok": False,
        "truncated": False,
        "error": detail,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, metavar="PATH",
        help="read a JSON object from PATH, or use - for standard input")
    parser.add_argument(
        "--output", type=Path, metavar="PATH",
        help="atomically write the JSON report to PATH")
    parser.add_argument("--eapi", help="EAPI for explicit dependency fields")
    parser.add_argument(
        "--use", action="append", metavar="[+|-]FLAG",
        help="set an explicit USE state; repeat for each enabled or disabled flag")
    for field in DEPENDENCY_FIELDS:
        parser.add_argument(
            f"--{field.lower()}", metavar="EXPRESSION",
            help=f"explicit {field} metadata")
    return parser.parse_args(argv)


def read_document(args: argparse.Namespace) -> tuple[Any, dict[str, Any]]:
    explicit = args.eapi is not None or args.use is not None or any(
        getattr(args, field.lower()) is not None for field in DEPENDENCY_FIELDS)
    if args.input is not None and explicit:
        raise AnalysisError(
            "invalid_cli", "--input cannot be combined with explicit metadata fields")
    if args.input is not None:
        try:
            if str(args.input) == "-":
                data = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            else:
                if args.input.stat().st_size > MAX_INPUT_BYTES:
                    raise AnalysisError(
                        "input_too_large",
                        f"JSON input exceeds {MAX_INPUT_BYTES} bytes")
                with args.input.open("rb") as handle:
                    data = handle.read(MAX_INPUT_BYTES + 1)
            if len(data) > MAX_INPUT_BYTES:
                raise AnalysisError(
                    "input_too_large",
                    f"JSON input exceeds {MAX_INPUT_BYTES} bytes")
        except OSError as exc:
            raise AnalysisError("input_read_failed", str(exc)) from exc
        provenance = input_provenance(
            data, "stdin" if str(args.input) == "-" else "json-file",
            "stdin" if str(args.input) == "-" else str(args.input))
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            error = AnalysisError(
                "invalid_input_encoding", "JSON input must be valid UTF-8")
            error.input_provenance = provenance
            raise error from exc
        try:
            return json.loads(content), provenance
        except json.JSONDecodeError as exc:
            error = AnalysisError(
                "invalid_json",
                f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            )
            error.input_provenance = provenance
            raise error from exc
    document: dict[str, Any] = {}
    if args.eapi is not None:
        document["eapi"] = args.eapi
    if args.use is not None:
        document["use"] = args.use
    for field in DEPENDENCY_FIELDS:
        value = getattr(args, field.lower())
        if value is not None:
            document[field] = value
    data = canonical_bytes(document)
    return document, input_provenance(data, "explicit-fields", "command-line")


def main(argv: list[str] | None = None) -> int:
    args: argparse.Namespace | None = None
    provenance: dict[str, Any] | None = None
    try:
        args = parse_args(argv)
        document, provenance = read_document(args)
        report = analyze(document, provenance=provenance)
    except AnalysisError as exc:
        report = error_report(exc, provenance)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args is not None and args.output is not None:
        try:
            atomic_write(args.output, payload)
        except OSError as exc:
            report = error_report(AnalysisError(
                "output_write_failed", f"cannot write output: {exc}"), provenance)
            payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
        else:
            print(f"Wrote complete dependency report to {args.output}.")
            return 0 if report["ok"] else 2
    print(payload, end="")
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
