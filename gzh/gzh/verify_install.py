from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping

from gzh.qa_evidence import (
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT,
    read_tool_version,
    run_evidence_command,
)
from gzh.repo import validate_overlay_root


MAX_ELOG_ENTRIES = 256
DEFAULT_VERIFY_TIMEOUT = 6 * 60 * 60
DEFAULT_VERIFY_MAX_OUTPUT_BYTES = MAX_OUTPUT_BYTES
_ARCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_PROFILE_RE = re.compile(r"[A-Za-z0-9/][A-Za-z0-9_+./:-]{0,511}\Z")


def atom_from_ebuild(ebuild: Path) -> str:
    ebuild = Path(ebuild).resolve()
    if ebuild.suffix != ".ebuild" or not ebuild.is_file() or len(ebuild.parents) < 3:
        raise ValueError(f"not an ebuild path: {ebuild}")
    package = ebuild.parent.name
    category = ebuild.parent.parent.name
    if not ebuild.name.startswith(f"{package}-"):
        raise ValueError(f"ebuild filename does not match its package: {ebuild}")
    try:
        root = validate_overlay_root(ebuild.parents[2])
    except RuntimeError as exc:
        raise ValueError(
            f"ebuild is not in a gentoo-zh development checkout: {ebuild}") from exc
    try:
        ebuild.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"ebuild is outside the overlay: {ebuild}") from exc
    return f"={category}/{ebuild.stem}::gentoo-zh"


def _environment_value(
        report: dict, *, pattern: re.Pattern[str],
) -> str | None:
    if (report.get("complete") is not True or report.get("truncated") is True
            or report.get("returncode") != 0):
        return None
    value = report["stdout"].strip()
    if not value or "\n" in value or "\r" in value or not pattern.fullmatch(value):
        return None
    return value


def _pending_elog_inventory(elog_dir: Path) -> dict:
    return {
        "path": str(elog_dir.absolute()),
        "exists": elog_dir.exists(),
        "entries": [],
        "complete": False,
        "truncated": False,
        "state": "not-collected",
        "errors": [],
    }


def _read_regular_elog(
        directory_fd: int, name: str, path: Path,
        expected: os.stat_result, maximum: int,
) -> bytes:
    flags = os.O_RDONLY
    for flag_name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag_name, 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if (not stat.S_ISREG(opened.st_mode)
                or identity != (expected.st_dev, expected.st_ino)):
            raise OSError(errno.ESTALE, "elog entry changed before it was opened")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        observed = os.fstat(descriptor)
        if ((observed.st_dev, observed.st_ino, observed.st_size,
             observed.st_mtime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size,
                    opened.st_mtime_ns)):
            raise OSError(errno.ESTALE, "elog entry changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _elog_inventory(elog_dir: Path, maximum: int) -> dict:
    inventory = _pending_elog_inventory(elog_dir)
    errors: list[str] = []
    truncated = False
    entries: list[dict] = []
    remaining = maximum
    directory_fd: int | None = None
    try:
        expected_directory = elog_dir.lstat()
        if not stat.S_ISDIR(expected_directory.st_mode):
            raise OSError(errno.ENOTDIR, "elog path is not a directory")
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        directory_fd = os.open(elog_dir, flags)
        opened_directory = os.fstat(directory_fd)
        if ((opened_directory.st_dev, opened_directory.st_ino)
                != (expected_directory.st_dev, expected_directory.st_ino)):
            raise OSError(errno.ESTALE, "elog directory changed before it was opened")
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        inventory.update({
            "state": "incomplete",
            "errors": [f"cannot list elog directory: {exc}"],
        })
        if directory_fd is not None:
            os.close(directory_fd)
        return inventory
    inventory.update({"exists": True, "state": "complete"})

    if len(names) > MAX_ELOG_ENTRIES:
        names = names[:MAX_ELOG_ENTRIES]
        truncated = True
        errors.append(f"elog inventory exceeds {MAX_ELOG_ENTRIES} entries")

    for name in names:
        path = elog_dir / name
        entry = {
            "path": str(path),
            "kind": "unknown",
            "size": None,
            "sha256": None,
            "text": "",
            "truncated": False,
        }
        try:
            item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISREG(item_stat.st_mode):
                entry["kind"] = "file"
                entry["size"] = item_stat.st_size
                content = _read_regular_elog(
                    directory_fd, name, path, item_stat, remaining)
                if len(content) > remaining or item_stat.st_size > remaining:
                    content = content[:remaining]
                    entry["truncated"] = True
                    truncated = True
                    errors.append(f"elog content exceeds evidence limit: {path}")
                else:
                    entry["sha256"] = hashlib.sha256(content).hexdigest()
                entry["text"] = content.decode(errors="replace")
                remaining -= len(content)
            elif stat.S_ISDIR(item_stat.st_mode):
                entry["kind"] = "directory"
                errors.append(f"unexpected directory in elog inventory: {path}")
            elif stat.S_ISLNK(item_stat.st_mode):
                entry["kind"] = "symlink"
                errors.append(f"unexpected symlink in elog inventory: {path}")
            else:
                entry["kind"] = "other"
                errors.append(f"unexpected special file in elog inventory: {path}")
        except OSError as exc:
            errors.append(f"cannot inspect elog entry {path}: {exc}")
        entries.append(entry)
    try:
        final_directory = os.fstat(directory_fd)
        if (sorted(os.listdir(directory_fd)) != names
                or final_directory.st_mtime_ns != opened_directory.st_mtime_ns
                or final_directory.st_ctime_ns != opened_directory.st_ctime_ns):
            errors.append("elog directory changed during inventory")
    except OSError as exc:
        errors.append(f"cannot recheck elog directory: {exc}")
    os.close(directory_fd)

    complete = not errors and not truncated
    inventory.update({
        "entries": entries,
        "complete": complete,
        "truncated": truncated,
        "state": "complete" if complete else "incomplete",
        "errors": errors,
    })
    return inventory


def _run_emerge(
        command: list[str], *, env: dict[str, str], timeout: int,
        max_output_bytes: int,
        runner: Callable[..., subprocess.CompletedProcess] | None,
) -> dict:
    return run_evidence_command(
        command, env=env, timeout=timeout, max_output_bytes=max_output_bytes,
        runner=runner)


def _step(name: str, execution: dict) -> dict:
    return {"name": name, **execution}


def run_verify_install(
        ebuild: Path, logdir: Path | None = None, *,
        timeout: int = DEFAULT_VERIFY_TIMEOUT,
        max_output_bytes: int = DEFAULT_VERIFY_MAX_OUTPUT_BYTES,
        environment: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = subprocess.run,
) -> dict:
    """Merge one exact ebuild with bounded overlay CI elog evidence."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not 256 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError(
            f"max output must be between 256 and {MAX_OUTPUT_BYTES} bytes")

    ebuild = Path(ebuild).resolve()
    atom = atom_from_ebuild(ebuild)
    if logdir is not None:
        requested_logdir = Path(logdir).expanduser()
        if requested_logdir.is_symlink():
            raise ValueError(f"log directory must not be a symlink: {requested_logdir}")
        logdir = requested_logdir.resolve()
        logdir.mkdir(parents=True, exist_ok=True)
    else:
        logdir = Path(tempfile.mkdtemp(prefix="gzh-verify-install-"))
    elog_dir = logdir / "elog"
    try:
        elog_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    env = dict(environment) if environment is not None else os.environ.copy()
    portage_environment = {
        "PORTAGE_ELOG_CLASSES": "qa warn error",
        "PORTAGE_ELOG_SYSTEM": "save",
        "PORTAGE_LOGDIR": str(logdir),
    }
    env.update(portage_environment)

    initial_elog = _elog_inventory(elog_dir, max_output_bytes)
    emerge_version = read_tool_version(
        ["emerge", "--version"], timeout=min(timeout, 30),
        max_output_bytes=min(max_output_bytes, 4096), env=env, runner=runner)
    profile_execution = run_evidence_command(
        ["eselect", "--brief", "profile", "show"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, env=env, runner=runner)
    arch_execution = run_evidence_command(
        ["portageq", "envvar", "ARCH"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, env=env, runner=runner)
    profile = _environment_value(profile_execution, pattern=_PROFILE_RE)
    arch = _environment_value(arch_execution, pattern=_ARCH_RE)
    environment_complete = (
        emerge_version["complete"] and profile is not None and arch is not None)
    isolated = initial_elog["complete"] and not initial_elog["entries"]

    steps: list[dict] = []
    dependency_elog = _pending_elog_inventory(elog_dir)
    dependency_elog_after_clear = _pending_elog_inventory(elog_dir)
    dependency_elog_clear_errors: list[str] = []
    target_elog = _pending_elog_inventory(elog_dir)
    failed_step: str | None = None
    errors: list[str] = []

    if not emerge_version["complete"]:
        errors.append("emerge version evidence is incomplete")
    if profile is None:
        errors.append("active profile evidence is incomplete")
    if arch is None:
        errors.append("ARCH evidence is incomplete")
    if not isolated:
        errors.append("isolated elog directory is not empty or could not be inspected")

    if not environment_complete or not isolated:
        failed_step = "preflight"
    else:
        source_options = ["--usepkg=n"]
        onlydeps_command = [
            "emerge", *source_options, "--onlydeps", atom]
        onlydeps = _run_emerge(
            onlydeps_command, env=env, timeout=timeout,
            max_output_bytes=max_output_bytes, runner=runner)
        steps.append(_step("onlydeps", onlydeps))
        dependency_elog = _elog_inventory(elog_dir, max_output_bytes)
        if onlydeps["complete"] is not True or onlydeps["truncated"] is True:
            failed_step = "onlydeps"
            errors.append("dependency merge produced incomplete evidence")
        elif onlydeps["returncode"] != 0:
            failed_step = "onlydeps"
            errors.append("dependency merge failed")
        elif not dependency_elog["complete"]:
            failed_step = "evidence"
            errors.append("dependency elog inventory is incomplete")
        elif any(
                entry["kind"] == "file"
                for entry in dependency_elog["entries"]):
            failed_step = "elog"
            errors.append("dependency merge produced a saved elog entry")
        else:
            merge_command = [
                "emerge", *source_options, "--oneshot", "--selective=n", atom]
            merge = _run_emerge(
                merge_command, env=env, timeout=timeout,
                max_output_bytes=max_output_bytes, runner=runner)
            steps.append(_step("merge", merge))
            target_elog = _elog_inventory(elog_dir, max_output_bytes)
            if merge["complete"] is not True or merge["truncated"] is True:
                failed_step = "merge"
                errors.append("target merge produced incomplete evidence")
            elif merge["returncode"] != 0:
                failed_step = "merge"
                errors.append("target merge failed")
            elif any(
                    entry["kind"] == "file"
                    for entry in target_elog["entries"]):
                failed_step = "elog"
                errors.append("target merge produced a saved elog entry")
            elif not target_elog["complete"]:
                failed_step = "evidence"
                errors.append("target elog inventory is incomplete")

    executions = [
        emerge_version["execution"], profile_execution, arch_execution,
        *(dict(step) for step in steps),
    ]
    truncated = (
        initial_elog["truncated"] or dependency_elog["truncated"]
        or dependency_elog_after_clear["truncated"] or target_elog["truncated"]
        or any(item.get("truncated") is True for item in executions))
    timed_out = any(item.get("timed_out") is True for item in executions)
    execution_complete = all(
        item.get("complete") is True and item.get("truncated") is not True
        for item in executions)
    inventory_complete = initial_elog["complete"]
    if steps:
        inventory_complete = inventory_complete and dependency_elog["complete"]
    if dependency_elog_after_clear["state"] != "not-collected":
        inventory_complete = (
            inventory_complete and dependency_elog_after_clear["complete"])
    if target_elog["state"] != "not-collected":
        inventory_complete = inventory_complete and target_elog["complete"]
    complete = environment_complete and execution_complete and inventory_complete
    ok = complete and failed_step is None

    if not environment_complete:
        state = "environment-incomplete"
    elif not isolated:
        state = "preflight-failed"
    elif timed_out:
        state = "timed-out"
    elif truncated:
        state = "truncated"
    elif failed_step is not None:
        state = "failed"
    elif complete:
        state = "passed"
    else:
        state = "incomplete"

    elog_files = [
        {"step": step_name, "path": entry["path"], "text": entry["text"],
         "size": entry["size"], "sha256": entry["sha256"],
         "truncated": entry["truncated"]}
        for step_name, inventory in (
            ("onlydeps", dependency_elog), ("merge", target_elog))
        for entry in inventory["entries"] if entry["kind"] == "file"
    ]
    commands = [
        emerge_version["execution"]["command"],
        profile_execution["command"], arch_execution["command"],
        *(step["command"] for step in steps),
    ]
    return {
        "schema_version": 1,
        "operation": "verify-install",
        "side_effectful": True,
        "atom": atom,
        "logdir": str(logdir),
        "options": {
            "source_only": True,
            "timeout_seconds": timeout,
            "max_output_bytes": max_output_bytes,
        },
        "tool": {"emerge": emerge_version},
        "environment": {
            "profile": {"value": profile, "execution": profile_execution},
            "arch": {"value": arch, "execution": arch_execution},
            "portage": portage_environment,
        },
        "commands": commands,
        "steps": steps,
        "initial_elog": initial_elog,
        "dependency_elog": {
            "observed": dependency_elog,
            "after_clear": dependency_elog_after_clear,
            "clear_errors": dependency_elog_clear_errors,
        },
        "elog": target_elog,
        "elog_files": elog_files,
        "ok": ok,
        "complete": complete,
        "timed_out": timed_out,
        "truncated": truncated,
        "state": state,
        "failed_step": failed_step,
        "errors": errors,
    }
