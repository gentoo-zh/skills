from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping

from gzh.qa_evidence import (
    DEFAULT_MAX_OUTPUT_BYTES,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT,
    identify_input,
    run_evidence_command,
)
from gzh.verify_install import _elog_inventory

PHASES = {
    "quick": ["clean", "unpack", "prepare", "configure"],
    "full": ["clean", "unpack", "prepare", "configure", "compile", "install"],
}

# QA notices that the emerge-on-PR elog gate flags but a bare `ebuild install` cannot
# adjudicate: an unresolved soname often just means the RDEPEND provider is not on the
# local box and will resolve in CI. Defer it to the dependency-resolving merge gate in
# finish-pipeline.md instead of failing the local build test on it.
_DEFERRED_QA = ("Unresolved soname",)
_ELOG_CLASSES = frozenset({"QA", "WARN", "ERROR"})
_MAX_ELOG_BYTES = 256 * 1024
_ARCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_PROFILE_RE = re.compile(r"[A-Za-z0-9/][A-Za-z0-9_+./:-]{0,511}\Z")
_SELECTED_ENVIRONMENT = (
    "ACCEPT_KEYWORDS", "EPREFIX", "FEATURES", "MAKEOPTS",
    "PORTAGE_CONFIGROOT", "ROOT", "SYSROOT", "USE",
)
DEFAULT_BUILD_TIMEOUT = 6 * 60 * 60
_MAX_SELECTED_ENVIRONMENT_BYTES = 16 * 1024


def scan_qa_notices(text: str) -> list[str]:
    """Advisory 'QA Notice' lines from a build stream, minus the deferred classes.

    Portage prints eqawarn/QA Notice to stderr, so callers should pass the COMBINED
    stdout+stderr (mirroring autobump.sh's `> build.log 2>&1`). This never changes the
    pass/fail verdict; it only surfaces notices for the -bin/prebuilt path to inspect.
    """
    return [ln for ln in (text or "").splitlines()
            if "QA Notice" in ln and not any(d in ln for d in _DEFERRED_QA)]


def _package_atom(ebuild: Path) -> str:
    package = ebuild.parent.name
    if ebuild.name.startswith(f"{package}-") and len(ebuild.parents) >= 2:
        return f"={ebuild.parent.parent.name}/{ebuild.stem}"
    return ebuild.stem


def _elog_records(inventory: Mapping, atom: str) -> list[dict]:
    records: list[dict] = []
    for entry in inventory.get("entries", []):
        if entry.get("kind") != "file":
            continue
        sections: list[tuple[str, str, list[str]]] = []
        current_class = "UNKNOWN"
        current_phase = "unknown"
        message: list[str] = []
        for line in entry.get("text", "").splitlines():
            prefix, separator, phase = line.partition(":")
            if separator and prefix in _ELOG_CLASSES:
                if message or current_class != "UNKNOWN":
                    sections.append((current_class, current_phase, message))
                current_class = prefix
                current_phase = phase.strip() or "unknown"
                message = []
            else:
                message.append(line)
        if message or current_class != "UNKNOWN":
            sections.append((current_class, current_phase, message))
        if not sections:
            sections.append(("UNKNOWN", "unknown", []))
        for elog_class, phase, lines in sections:
            records.append({
                "class": elog_class.lower(),
                "atom": atom,
                "path": entry["path"],
                "phase": phase,
                "message": "\n".join(lines).strip(),
                "size": entry["size"],
                "sha256": entry["sha256"],
                "truncated": entry["truncated"],
            })
    return records


def _environment_value(report: Mapping, pattern: re.Pattern[str]) -> str | None:
    if (report.get("complete") is not True or report.get("truncated") is True
            or report.get("returncode") != 0):
        return None
    value = str(report.get("stdout", "")).strip()
    if not value or "\n" in value or "\r" in value or not pattern.fullmatch(value):
        return None
    return value


def _selected_environment(environment: Mapping[str, str]) -> tuple[dict, bool]:
    selected: dict[str, str | dict] = {}
    remaining = _MAX_SELECTED_ENVIRONMENT_BYTES
    keys = (*_SELECTED_ENVIRONMENT,
            "PORTAGE_ELOG_CLASSES", "PORTAGE_ELOG_SYSTEM", "PORTAGE_LOGDIR")
    for key in keys:
        if key not in environment:
            continue
        value = environment[key]
        encoded = value.encode()
        if len(encoded) > remaining:
            selected[key] = {
                "bytes": len(encoded),
                "omitted": True,
                "sha256": hashlib.sha256(encoded).hexdigest(),
            }
            continue
        selected[key] = value
        remaining -= len(encoded)
    return selected, all(isinstance(value, str) for value in selected.values())


def run_build_test(ebuild: Path, level: str = "full",
                   runner: Callable[..., subprocess.CompletedProcess] | None = None,
                   logdir: Path | None = None, *,
                   timeout: int = DEFAULT_BUILD_TIMEOUT,
                   max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                   environment: Mapping[str, str] | None = None) -> dict:
    if level not in {*PHASES, "none"}:
        raise ValueError(f"unsupported build level: {level}")
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not 256 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError(
            f"max output must be between 256 and {MAX_OUTPUT_BYTES} bytes")
    ebuild = Path(ebuild).resolve()
    atom = _package_atom(ebuild)
    input_record = identify_input(ebuild)
    if level == "none":
        return {
            "schema_version": 1, "operation": "build-test", "atom": atom,
            "input": input_record, "ok": True, "complete": True,
            "level": level, "skipped": True, "reason": "level=none",
            "state": "skipped", "failed_phase": None,
            "failure_reason": None, "log_path": None, "evidence_dir": None,
            "stdout": "", "stderr": "", "stdout_bytes": 0,
            "stderr_bytes": 0, "returncode": 0, "qa_notices": [],
            "commands": [], "steps": [], "elog_records": [],
            "elog_files": [], "truncated": False, "timed_out": False,
            "errors": [],
        }
    if logdir is None:
        logdir = Path(tempfile.mkdtemp(prefix="gzh-build-elog-")).resolve()
    else:
        requested_logdir = Path(logdir).expanduser()
        if requested_logdir.is_symlink():
            raise ValueError(f"log directory must not be a symlink: {requested_logdir}")
        logdir = requested_logdir.resolve()
        logdir.mkdir(parents=True, exist_ok=True)
    elog_dir = logdir / "elog"
    elog_dir.mkdir(mode=0o700, exist_ok=True)
    initial_elog = _elog_inventory(elog_dir, _MAX_ELOG_BYTES)
    if not initial_elog["complete"] or initial_elog["entries"]:
        return {
            "ok": False,
            "complete": initial_elog["complete"],
            "level": level,
            "skipped": False,
            "reason": None,
            "failure_reason": "elog_preflight_failed",
            "failed_phase": None,
            "log_path": str(logdir),
            "evidence_dir": str(logdir),
            "schema_version": 1,
            "operation": "build-test",
            "atom": atom,
            "input": input_record,
            "state": "preflight-failed",
            "stdout": "",
            "stderr": "",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
            "returncode": None,
            "qa_notices": [],
            "commands": [],
            "steps": [],
            "elog_files": [],
            "truncated": initial_elog["truncated"],
            "timed_out": False,
            "errors": ["isolated elog directory is not empty or could not be inspected"],
            "elog_inventory": initial_elog,
            "elog_records": _elog_records(initial_elog, atom),
        }
    base_environment = (
        dict(environment) if environment is not None else os.environ.copy())
    environment = {
        **base_environment,
        "PORTAGE_ELOG_CLASSES": "qa warn error",
        "PORTAGE_ELOG_SYSTEM": "save",
        "PORTAGE_LOGDIR": str(logdir),
    }
    arch_execution = run_evidence_command(
        ["portageq", "envvar", "ARCH"], timeout=min(timeout, 30),
        max_output_bytes=min(max_output_bytes, 4096), env=environment,
        runner=runner)
    profile_execution = run_evidence_command(
        ["eselect", "--brief", "profile", "show"],
        timeout=min(timeout, 30),
        max_output_bytes=min(max_output_bytes, 4096), env=environment,
        runner=runner)
    arch = _environment_value(arch_execution, _ARCH_RE)
    profile = _environment_value(profile_execution, _PROFILE_RE)
    selected_environment, selected_environment_complete = _selected_environment(environment)
    environment_complete = (
        arch is not None and profile is not None and selected_environment_complete)
    phases = PHASES[level] if environment_complete else []
    failed_phase = None
    phase_steps: list[dict] = []
    for phase in phases:
        args = ["ebuild", str(ebuild), phase]
        execution = run_evidence_command(
            args, timeout=timeout, max_output_bytes=max_output_bytes,
            env=environment, runner=runner)
        phase_steps.append({"name": phase, **execution})
        if (execution["complete"] is not True
                or execution["truncated"] is True
                or execution["returncode"] != 0):
            failed_phase = phase
            break
    elog_inventory = _elog_inventory(elog_dir, _MAX_ELOG_BYTES)
    elog_records = _elog_records(elog_inventory, atom)
    elog_gate_failed = any(
        entry.get("kind") == "file"
        for entry in elog_inventory["entries"])
    phase_execution_complete = all(
        step["complete"] is True and step["truncated"] is not True
        for step in phase_steps)
    if failed_phase is not None and (
            phase_steps[-1]["complete"] is not True
            or phase_steps[-1]["truncated"] is True):
        failure_reason = "phase_evidence_incomplete"
    elif failed_phase is not None:
        failure_reason = "phase_failed"
    elif not elog_inventory["complete"]:
        failure_reason = "elog_evidence_incomplete"
    elif elog_gate_failed:
        failure_reason = "elog_gate_failed"
    elif not environment_complete:
        failure_reason = "environment_evidence_incomplete"
    else:
        failure_reason = None
    executions = [arch_execution, profile_execution, *phase_steps]
    truncated = (
        elog_inventory["truncated"]
        or any(item["truncated"] is True for item in executions))
    timed_out = any(item["timed_out"] is True for item in executions)
    complete = (
        environment_complete and phase_execution_complete
        and elog_inventory["complete"] and not truncated)
    stdout = "".join(step["stdout"] for step in phase_steps)
    stderr = "".join(step["stderr"] for step in phase_steps)
    errors = []
    if arch is None:
        errors.append("ARCH evidence is incomplete")
    if profile is None:
        errors.append("active profile evidence is incomplete")
    if not selected_environment_complete:
        errors.append("selected environment evidence exceeds its bounded limit")
    if failure_reason == "phase_evidence_incomplete":
        errors.append(f"{failed_phase} produced incomplete command evidence")
    if failure_reason == "phase_failed":
        errors.append(f"{failed_phase} failed")
    if not elog_inventory["complete"]:
        errors.append("elog evidence is incomplete")
    if elog_gate_failed:
        errors.append("build produced a saved qa, warn, or error elog entry")
    if timed_out:
        state = "timed-out"
    elif truncated:
        state = "truncated"
    elif failure_reason is not None:
        state = "failed"
    elif complete:
        state = "passed"
    else:
        state = "incomplete"
    elog_files = [
        {key: entry[key] for key in ("path", "size", "sha256", "truncated")}
        for entry in elog_inventory["entries"] if entry["kind"] == "file"
    ]
    return {
        "schema_version": 1,
        "operation": "build-test",
        "atom": atom,
        "input": input_record,
        "ok": failure_reason is None and complete,
        "complete": complete,
        "level": level,
        "failed_phase": failed_phase,
        "failure_reason": failure_reason,
        "state": state,
        "log_path": str(logdir),
        "evidence_dir": str(logdir),
        "options": {
            "timeout_seconds": timeout,
            "max_output_bytes_per_command": max_output_bytes,
            "max_elog_bytes": _MAX_ELOG_BYTES,
            "max_selected_environment_bytes": _MAX_SELECTED_ENVIRONMENT_BYTES,
        },
        "environment": {
            "arch": {"value": arch, "execution": arch_execution},
            "profile": {"value": profile, "execution": profile_execution},
            "selected": selected_environment,
            "scope": "allowlisted Portage build variables; inherited environment omitted",
            "complete": environment_complete,
        },
        "commands": [item["command"] for item in executions],
        "steps": phase_steps,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_bytes": sum(step["stdout_bytes"] for step in phase_steps),
        "stderr_bytes": sum(step["stderr_bytes"] for step in phase_steps),
        "returncode": phase_steps[-1]["returncode"] if phase_steps else None,
        "skipped": False,
        "qa_notices": scan_qa_notices(stdout + stderr),
        "elog_inventory": elog_inventory,
        "elog_records": elog_records,
        "elog_files": elog_files,
        "truncated": truncated,
        "timed_out": timed_out,
        "errors": errors,
    }
