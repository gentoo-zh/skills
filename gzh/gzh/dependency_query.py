from __future__ import annotations

import hashlib
import subprocess
from typing import Callable

from portage.dep import Atom
from portage.exception import InvalidAtom
from portage.versions import catpkgsplit

from gzh.qa_evidence import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT,
    read_tool_version,
    run_evidence_command,
)


PQUERY_SCOPE = {
    "abi_correctness": False,
    "active_profile_resolution": False,
    "dependency_view": "raw",
    "relationship": "potential-direct-reverse-dependency",
    "repositories": "configured-ebuild-repositories",
    "transitive_resolution": False,
}
MAX_RESULT_RECORDS = 16_384
MAX_MALFORMED_PREVIEW = 512


def _validate_atom(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("atom must be a non-empty string without surrounding whitespace")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("atom must not contain whitespace or control characters")
    try:
        return str(Atom(value, allow_repo=True))
    except InvalidAtom as exc:
        raise ValueError(f"invalid package atom: {value}") from exc


def _parse_record(line: str) -> dict:
    if line != line.strip() or line.count("::") != 1:
        raise ValueError("expected category/package-version::repository")
    cpv, repository = line.split("::", 1)
    split = catpkgsplit(cpv)
    if split is None:
        raise ValueError("invalid package version")
    category, package, version, revision = split
    exact_atom = f"={cpv}::{repository}"
    try:
        parsed = Atom(exact_atom, allow_repo=True)
    except InvalidAtom as exc:
        raise ValueError("invalid exact package or repository") from exc
    if parsed.blocker or parsed.repo != repository:
        raise ValueError("invalid exact package or repository")
    full_version = version if revision == "r0" else f"{version}-{revision}"
    return {
        "atom": exact_atom,
        "category": category,
        "cpv": cpv,
        "package": package,
        "repository": repository,
        "revision": revision,
        "version": full_version,
    }


def _parse_output(stdout: str) -> tuple[list[dict], list[dict]]:
    records: list[dict] = []
    malformed: list[dict] = []
    for number, line in enumerate(stdout.splitlines(), start=1):
        if len(records) + len(malformed) >= MAX_RESULT_RECORDS:
            malformed.append({
                "line": number,
                "message": f"output exceeds {MAX_RESULT_RECORDS} records",
                "sha256": hashlib.sha256(line.encode()).hexdigest(),
                "preview": line[:MAX_MALFORMED_PREVIEW],
            })
            break
        try:
            records.append(_parse_record(line))
        except ValueError as exc:
            malformed.append({
                "line": number,
                "message": str(exc),
                "sha256": hashlib.sha256(line.encode()).hexdigest(),
                "preview": line[:MAX_MALFORMED_PREVIEW],
            })
    return records, malformed


def _base_report(requested_atom: object) -> dict:
    return {
        "schema_version": 1,
        "tool": {
            "name": "pquery",
            "version": None,
            "version_command": ["pquery", "--version"],
            "version_evidence": None,
        },
        "query": {
            "requested_atom": requested_atom,
            "validated_atom": None,
        },
        "scope": dict(PQUERY_SCOPE),
        "command": None,
        "execution": None,
        "stdout": "",
        "stderr": "",
        "results": [],
        "partial_results": [],
        "malformed_output": [],
        "errors": [],
        "complete": False,
        "ok": False,
        "state": "incomplete",
        "timed_out": False,
        "truncated": False,
    }


def query_reverse_dependencies(
    atom: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    """Collect raw potential direct reverse dependencies from ebuild repos."""
    report = _base_report(atom)
    try:
        validated_atom = _validate_atom(atom)
    except ValueError as exc:
        report.update(state="invalid-input")
        report["errors"].append({
            "stage": "input",
            "type": type(exc).__name__,
            "message": str(exc),
        })
        return report

    command = [
        "pquery",
        "--raw",
        "--ebuild-repos",
        "--cpv",
        "-R",
        "--restrict-revdep",
        validated_atom,
    ]
    report["query"]["validated_atom"] = validated_atom
    report["command"] = command

    version = read_tool_version(
        ["pquery", "--version"],
        timeout=min(timeout, 30),
        max_output_bytes=min(max_output_bytes, 1024),
        runner=runner,
    )
    report["tool"].update({
        "version": version["version"],
        "version_evidence": version["execution"],
    })
    if not version["complete"]:
        execution = version["execution"]
        report.update({
            "state": "tool-incomplete",
            "stdout": execution["stdout"],
            "stderr": execution["stderr"],
            "timed_out": execution["timed_out"],
            "truncated": execution["truncated"],
        })
        error = execution.get("error") or {
            "type": "ToolVersionError",
            "message": "pquery version command failed",
        }
        report["errors"].append({"stage": "tool-version", **error})
        return report

    execution = run_evidence_command(
        command,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
        runner=runner,
    )
    report.update({
        "execution": execution,
        "stdout": execution["stdout"],
        "stderr": execution["stderr"],
        "timed_out": execution["timed_out"],
        "truncated": execution["truncated"],
    })
    if execution["complete"] is not True or execution["truncated"] is True:
        report["state"] = execution["state"]
        error = execution.get("error") or {
            "type": "IncompleteExecution",
            "message": "pquery execution evidence is incomplete",
        }
        report["errors"].append({"stage": "query", **error})
        return report
    if execution["returncode"] != 0:
        report["state"] = "failed"
        report["errors"].append({
            "stage": "query",
            "type": "CommandFailed",
            "message": f"pquery exited with status {execution['returncode']}",
        })
        return report

    records, malformed = _parse_output(execution["stdout"])
    if malformed:
        report.update({
            "state": "malformed-output",
            "partial_results": records,
            "malformed_output": malformed,
        })
        report["errors"].append({
            "stage": "output",
            "type": "MalformedOutput",
            "message": "pquery output contains malformed records",
        })
        return report

    report.update({
        "complete": True,
        "ok": True,
        "state": "complete",
        "results": records,
    })
    return report
