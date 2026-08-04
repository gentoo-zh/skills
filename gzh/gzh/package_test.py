from __future__ import annotations

import hashlib
import re
import stat
import subprocess
from pathlib import Path
from typing import Callable

from portage.dep import Atom, InvalidAtom

from gzh.qa_evidence import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT,
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT,
    read_tool_version,
    run_evidence_command,
)


MAX_USE_COMBOS = 32
MAX_LOG_ENTRIES = 256
PORTAGE_CONFIG_ROOT = Path("/etc/portage")
USE_PREFERENCES = {
    "default": "--use-default",
    "random": "--use-random",
    "expand-random": "--use-expand-random",
}
_JOB_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_ARCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")


def _validate_atom(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("atom must be a non-empty string without surrounding whitespace")
    if any(character.isspace() or ord(character) < 32 for character in value):
        raise ValueError("atom must not contain whitespace or control characters")
    try:
        atom = Atom(value, allow_repo=True)
    except InvalidAtom as exc:
        raise ValueError(f"invalid package atom: {value}") from exc
    if atom.blocker or atom.use:
        raise ValueError("blockers and USE dependencies are not valid package test targets")
    return value


def _validate_job_name(value: str) -> str:
    if not isinstance(value, str) or not _JOB_NAME_RE.fullmatch(value):
        raise ValueError(
            "job name must contain 1-64 ASCII letters, digits, dots, underscores, or hyphens")
    if value in {".", ".."}:
        raise ValueError("job name must not be a relative path component")
    return value


def _default_job_name(atom: str, evidence_dir: Path) -> str:
    package = Atom(atom, allow_repo=True).cp.split("/", 1)[1]
    safe_package = re.sub(r"[^A-Za-z0-9._-]", "-", package)[:32].strip(".-_")
    digest = hashlib.sha256(
        f"{atom}\0{evidence_dir.resolve()}".encode()).hexdigest()[:12]
    return f"gzh-{safe_package or 'package'}-{digest}"


def _empty_execution(command: list[str], *, state: str, message: str) -> dict:
    return {
        "command": command,
        "cwd": None,
        "returncode": None,
        "duration_seconds": None,
        "stdout": "",
        "stderr": "",
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "timed_out": False,
        "complete": False,
        "truncated": False,
        "skipped": True,
        "state": state,
        "error": {"type": "Skipped", "message": message},
    }


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def _portage_config_paths(job_name: str) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return the exact paths created by pkgdev tatt's default generator."""
    files = (
        PORTAGE_CONFIG_ROOT / "package.accept_keywords" / f"pkgdev_tatt_{job_name}.keywords",
        PORTAGE_CONFIG_ROOT / "env" / f"pkgdev_tatt_{job_name}_no_test",
        PORTAGE_CONFIG_ROOT / "env" / f"pkgdev_tatt_{job_name}_test",
    )
    directories = (
        PORTAGE_CONFIG_ROOT / "package.use" / f"pkgdev_tatt_{job_name}",
        PORTAGE_CONFIG_ROOT / "package.env" / f"pkgdev_tatt_{job_name}",
    )
    return files, directories


def _file_record(path: Path, maximum: int) -> dict:
    record = {
        "path": str(path),
        "exists": path.exists(),
        "regular": False,
        "size": None,
        "sha256": None,
        "content": "",
        "truncated": False,
    }
    if not _regular_file(path):
        return record
    size = path.stat().st_size
    digest = hashlib.sha256()
    content = bytearray()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
            if len(content) < maximum:
                content.extend(chunk[:maximum - len(content)])
    record.update({
        "regular": True,
        "size": size,
        "sha256": digest.hexdigest(),
        "content": bytes(content).decode(errors="replace"),
        "truncated": size > maximum,
    })
    return record


def _script_record(path: Path) -> dict:
    record = {
        "path": str(path),
        "exists": path.exists(),
        "regular": False,
        "executable": False,
        "size": None,
        "sha256": None,
    }
    if not _regular_file(path):
        return record
    path_stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    record.update({
        "regular": True,
        "executable": bool(path_stat.st_mode & stat.S_IXUSR),
        "size": path_stat.st_size,
        "sha256": digest.hexdigest(),
    })
    return record


def _logs_record(path: Path) -> dict:
    entries: list[dict] = []
    truncated = False
    if path.is_dir():
        for index, item in enumerate(sorted(path.rglob("*"))):
            if index >= MAX_LOG_ENTRIES:
                truncated = True
                break
            try:
                relative = str(item.relative_to(path))
                item_stat = item.lstat()
            except (FileNotFoundError, OSError):
                truncated = True
                continue
            entries.append({
                "path": relative,
                "kind": ("file" if stat.S_ISREG(item_stat.st_mode)
                         else "directory" if stat.S_ISDIR(item_stat.st_mode)
                         else "symlink" if stat.S_ISLNK(item_stat.st_mode)
                         else "other"),
                "size": item_stat.st_size if stat.S_ISREG(item_stat.st_mode) else None,
            })
    return {
        "path": str(path),
        "exists": path.exists(),
        "entries": entries,
        "truncated": truncated,
    }


def _execution_ok(report: dict) -> bool:
    return (report.get("complete") is True
            and report.get("truncated") is False
            and report.get("returncode") == 0)


def _environment_value(report: dict, *, arch: bool = False) -> str | None:
    if not _execution_ok(report):
        return None
    value = report["stdout"].strip()
    if not value or (arch and not _ARCH_RE.fullmatch(value)):
        return None
    return value


def _fallback_cleanup(
        files: tuple[Path, ...], directories: tuple[Path, ...], *, cwd: Path,
        timeout: int, max_output_bytes: int,
        runner: Callable[..., subprocess.CompletedProcess] | None,
) -> dict:
    targets = [path for path in (*files, *directories) if _path_exists(path)]
    commands = ([
        "rm", "-d", "-f", "--", *(str(path) for path in targets),
    ],) if targets else ()
    steps = [
        run_evidence_command(
            command, cwd=cwd, timeout=timeout,
            max_output_bytes=max_output_bytes, runner=runner)
        for command in commands
    ]
    remaining = [
        str(path) for path in (*files, *directories) if _path_exists(path)]
    complete = all(_execution_ok(step) for step in steps) and not remaining
    return {
        "command": list(commands[0]) if commands else [],
        "commands": [list(command) for command in commands],
        "cwd": str(cwd),
        "returncode": 0 if complete else 1,
        "duration_seconds": round(sum(
            step.get("duration_seconds") or 0 for step in steps), 3),
        "stdout": "".join(step.get("stdout", "") for step in steps),
        "stderr": "".join(step.get("stderr", "") for step in steps),
        "stdout_bytes": sum(step.get("stdout_bytes", 0) for step in steps),
        "stderr_bytes": sum(step.get("stderr_bytes", 0) for step in steps),
        "timed_out": any(step.get("timed_out") is True for step in steps),
        "complete": complete,
        "truncated": any(step.get("truncated") is True for step in steps),
        "skipped": False,
        "state": "complete" if complete else "incomplete",
        "error": (None if complete else {
            "type": "FallbackCleanupFailed",
            "message": "exact Portage configuration cleanup did not complete",
        }),
        "fallback": True,
        "steps": steps,
        "remaining_paths": remaining,
    }


def _skipped_report(atom: str, evidence_dir: Path, job_name: str,
                    use_combos: int, use_preference: str) -> dict:
    reason = "package testing requires allow_side_effects=True"
    skipped = _empty_execution([], state="skipped", message=reason)
    return {
        "schema_version": 1,
        "operation": "package-test",
        "side_effectful": True,
        "atom": atom,
        "job_name": job_name,
        "options": {
            "test": True,
            "use_combos": use_combos,
            "use_preference": use_preference,
        },
        "evidence_dir": str(evidence_dir.resolve()),
        "tool": {"pkgdev": None},
        "environment": {"profile": None, "arch": None},
        "generation": skipped,
        "job": skipped.copy(),
        "cleanup": skipped.copy(),
        "artifacts": {
            "script": {"path": None, "exists": False},
            "report": {"path": None, "exists": False},
            "logs": {"path": None, "exists": False, "entries": []},
        },
        "ok": False,
        "complete": False,
        "truncated": False,
        "timed_out": False,
        "skipped": True,
        "state": "skipped",
        "errors": [reason],
    }


def run_package_test(
        atom: str, evidence_dir: Path, *, allow_side_effects: bool = False,
        job_name: str | None = None, use_combos: int = 0,
        use_preference: str = "default", timeout: int = DEFAULT_TIMEOUT,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    """Generate and run a side-effectful pkgdev tatt package test job.

    The generated job modifies Portage configuration and merges the target package.
    Callers must explicitly acknowledge those effects. The function never uses a shell
    and executes only the regular script at the exact path selected before generation.
    """
    atom = _validate_atom(atom)
    evidence_dir = Path(evidence_dir).expanduser()
    if not isinstance(use_combos, int) or isinstance(use_combos, bool):
        raise ValueError("use combos must be an integer")
    if not 0 <= use_combos <= MAX_USE_COMBOS:
        raise ValueError(f"use combos must be between 0 and {MAX_USE_COMBOS}")
    if use_preference not in USE_PREFERENCES:
        choices = ", ".join(sorted(USE_PREFERENCES))
        raise ValueError(f"use preference must be one of: {choices}")
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not 256 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError(
            f"max output must be between 256 and {MAX_OUTPUT_BYTES} bytes")
    selected_job_name = _validate_job_name(
        job_name or _default_job_name(atom, evidence_dir))
    if not allow_side_effects:
        return _skipped_report(
            atom, evidence_dir, selected_job_name, use_combos, use_preference)

    if _path_exists(evidence_dir):
        raise FileExistsError(f"evidence directory already exists: {evidence_dir}")
    resolved_dir = evidence_dir.resolve()
    resolved_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    logs_dir = resolved_dir / "logs"
    logs_dir.mkdir(mode=0o700)
    script_path = resolved_dir / f"{selected_job_name}.sh"
    report_path = resolved_dir / f"{selected_job_name}.report"
    config_files, config_directories = _portage_config_paths(selected_job_name)
    preexisting_config = [
        str(path) for path in (*config_files, *config_directories)
        if _path_exists(path)]

    version = read_tool_version(
        ["pkgdev", "--version"], timeout=min(timeout, 30),
        max_output_bytes=min(max_output_bytes, 4096), runner=runner)
    profile_execution = run_evidence_command(
        ["eselect", "profile", "show"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, runner=runner)
    arch_execution = run_evidence_command(
        ["portageq", "envvar", "ARCH"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, runner=runner)
    profile = _environment_value(profile_execution)
    arch = _environment_value(arch_execution, arch=True)

    generation_command = [
        "pkgdev", "tatt", "--config", "false", "--color", "false",
        "--job-name", selected_job_name, "--test",
        "--logs-dir", str(logs_dir),
    ]
    if use_combos:
        generation_command.extend(["--use-combos", str(use_combos)])
    generation_command.extend([USE_PREFERENCES[use_preference], "--packages", atom])

    generated_script = _script_record(script_path)
    config_collision = bool(preexisting_config)
    if not version["complete"] or profile is None or arch is None:
        reason = "pkgdev version and active profile/ARCH evidence must be complete"
        generation = _empty_execution(
            generation_command, state="environment-incomplete", message=reason)
        job = _empty_execution([], state="skipped", message=reason)
        cleanup = _empty_execution([], state="skipped", message=reason)
    elif config_collision:
        reason = "job-specific Portage configuration paths already exist"
        generation = _empty_execution(
            generation_command, state="config-collision", message=reason)
        job = _empty_execution([], state="skipped", message=reason)
        cleanup = _empty_execution([], state="skipped", message=reason)
    else:
        generation = run_evidence_command(
            generation_command, cwd=resolved_dir, timeout=min(timeout, 300),
            max_output_bytes=max_output_bytes, runner=runner)
        generated_script = _script_record(script_path)
        valid_script = generated_script["regular"] and generated_script["executable"]
        if _execution_ok(generation) and valid_script:
            job_reason = None
        elif valid_script:
            job_reason = "generation did not complete successfully"
        else:
            job_reason = "generated job is missing, substituted, or not executable"

        cleanup_command = [str(script_path), "--clean"]
        if job_reason is None:
            try:
                job = run_evidence_command(
                    [str(script_path)], cwd=resolved_dir, timeout=timeout,
                    max_output_bytes=max_output_bytes, runner=runner)
            finally:
                cleanup = run_evidence_command(
                    cleanup_command, cwd=resolved_dir, timeout=min(timeout, 300),
                    max_output_bytes=max_output_bytes, runner=runner)
        else:
            job = _empty_execution(
                [str(script_path)], state="skipped", message=job_reason)
            if valid_script:
                cleanup = run_evidence_command(
                    cleanup_command, cwd=resolved_dir, timeout=min(timeout, 300),
                    max_output_bytes=max_output_bytes, runner=runner)
            else:
                cleanup = _fallback_cleanup(
                    config_files, config_directories, cwd=resolved_dir,
                    timeout=min(timeout, 300), max_output_bytes=max_output_bytes,
                    runner=runner)

    report_record = _file_record(report_path, max_output_bytes)
    logs_record = _logs_record(logs_dir)
    script_exists = script_path.exists() or script_path.is_symlink()
    remaining_config = [
        str(path) for path in (*config_files, *config_directories)
        if _path_exists(path)]
    environment_complete = version["complete"] and profile is not None and arch is not None
    generation_attempted = generation.get("skipped") is not True
    cleanup_complete = (
        not generation_attempted
        or (_execution_ok(cleanup) and not script_exists and not remaining_config))
    report_complete = report_record["regular"] and not report_record["truncated"]
    executions = [
        version["execution"], profile_execution, arch_execution,
        generation, job, cleanup,
    ]
    truncated = (report_record["truncated"] or logs_record["truncated"]
                 or any(item.get("truncated") is True for item in executions))
    timed_out = any(item.get("timed_out") is True for item in executions)
    complete = (
        environment_complete
        and _execution_ok(generation)
        and job.get("complete") is True and not job.get("truncated")
        and cleanup_complete and report_complete and not logs_record["truncated"])
    ok = complete and job.get("returncode") == 0

    errors: list[str] = []
    if not version["complete"]:
        errors.append("pkgdev version evidence is incomplete")
    if profile is None:
        errors.append("active profile evidence is incomplete")
    if arch is None:
        errors.append("ARCH evidence is incomplete")
    if config_collision:
        errors.append("job-specific Portage configuration paths already exist")
    elif not _execution_ok(generation):
        errors.append("pkgdev tatt generation failed or produced incomplete evidence")
    if job.get("skipped"):
        errors.append("generated job was not executed")
    elif not _execution_ok(job):
        errors.append("package test job failed or produced incomplete evidence")
    if generation_attempted and not cleanup_complete:
        errors.append("generated job cleanup is incomplete")
    if not report_complete:
        errors.append("package test report is missing, invalid, or truncated")
    if logs_record["truncated"]:
        errors.append("package test log inventory is truncated")

    if not environment_complete:
        state = "environment-incomplete"
    elif config_collision:
        state = "config-collision"
    elif not cleanup_complete:
        state = "cleanup-failed"
    elif timed_out:
        state = "timed-out"
    elif truncated:
        state = "truncated"
    elif not _execution_ok(generation):
        state = "generation-failed"
    elif job.get("skipped"):
        state = "job-missing"
    elif not report_complete:
        state = "report-missing"
    elif job.get("returncode") != 0:
        state = "failed"
    elif complete:
        state = "passed"
    else:
        state = "incomplete"

    return {
        "schema_version": 1,
        "operation": "package-test",
        "side_effectful": True,
        "atom": atom,
        "job_name": selected_job_name,
        "options": {
            "test": True,
            "use_combos": use_combos,
            "use_preference": use_preference,
            "timeout_seconds": timeout,
            "max_output_bytes": max_output_bytes,
        },
        "evidence_dir": str(resolved_dir),
        "tool": {"pkgdev": version},
        "environment": {
            "profile": {"value": profile, "execution": profile_execution},
            "arch": {"value": arch, "execution": arch_execution},
        },
        "generation": generation,
        "job": job,
        "cleanup": cleanup,
        "artifacts": {
            "script": generated_script | {"exists_after_cleanup": script_exists},
            "report": report_record,
            "logs": logs_record,
            "portage_config": {
                "paths": [
                    str(path) for path in (*config_files, *config_directories)],
                "preexisting": preexisting_config,
                "remaining_after_cleanup": remaining_config,
            },
        },
        "ok": ok,
        "complete": complete,
        "truncated": truncated,
        "timed_out": timed_out,
        "skipped": False,
        "state": state,
        "errors": errors,
    }
