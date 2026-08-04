from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Callable

from gzh.binary_qa import MAX_ELF_FILES, _run, inspect_binaries


MAX_IMAGE_ENTRIES = 100_000


def _kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISFIFO(mode):
        return "fifo"
    if stat.S_ISSOCK(mode):
        return "socket"
    if stat.S_ISCHR(mode):
        return "character-device"
    if stat.S_ISBLK(mode):
        return "block-device"
    return "unknown"


def _relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return "/" + relative if relative else "/"


def _symlink_target(path: Path, root: Path) -> tuple[str, bool, bool]:
    target = os.readlink(path)
    candidate = (root / target.lstrip("/")) if target.startswith("/") else (path.parent / target)
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
        escapes = False
    except ValueError:
        escapes = True
    return target, escapes, candidate.exists()


def _entry(path: Path, root: Path) -> tuple[dict, list[dict]]:
    info = path.lstat()
    kind = _kind(info.st_mode)
    record = {
        "gid": info.st_gid,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "path": _relative(path, root),
        "size": info.st_size,
        "type": kind,
        "uid": info.st_uid,
    }
    findings: list[dict] = []
    if kind == "symlink":
        target, escapes, exists = _symlink_target(path, root)
        record["target"] = target
        if escapes:
            findings.append({
                "code": "escaping-symlink",
                "message": "relative symlink escapes the inspected image root",
                "path": record["path"],
                "severity": "error",
            })
        elif not exists:
            findings.append({
                "code": "unresolved-symlink",
                "message": "symlink target is absent from the inspected image",
                "path": record["path"],
                "severity": "info",
            })
    if info.st_mode & stat.S_IWOTH:
        findings.append({
            "code": "world-writable",
            "message": "installed path is world-writable",
            "path": record["path"],
            "severity": "warning",
        })
    if info.st_mode & (stat.S_ISUID | stat.S_ISGID):
        findings.append({
            "code": "privileged-mode",
            "message": "installed path has setuid or setgid mode",
            "path": record["path"],
            "severity": "warning",
        })
    if kind in {"socket", "character-device", "block-device", "unknown"}:
        findings.append({
            "code": "special-file",
            "message": f"installed image contains a {kind}",
            "path": record["path"],
            "severity": "error",
        })
    return record, findings


def _walk_image(root: Path, limit: int) -> tuple[list[dict], list[dict], bool]:
    entries: list[dict] = []
    findings: list[dict] = []
    truncated = False
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        files.sort()
        names = [*dirs, *files]
        for name in names:
            if len(entries) >= limit:
                return entries, findings, True
            path = Path(current) / name
            try:
                record, observed = _entry(path, root)
            except OSError as exc:
                record = {"path": _relative(path, root), "type": "unreadable"}
                observed = [{
                    "code": "unreadable-path",
                    "message": str(exc),
                    "path": record["path"],
                    "severity": "error",
                }]
            entries.append(record)
            findings.extend(observed)
    return entries, findings, truncated


def _validate_files(
    root: Path,
    entries: list[dict],
    *,
    runner: Callable,
) -> list[dict]:
    validators: list[dict] = []
    for entry in entries:
        path = entry["path"]
        if entry.get("type") != "file":
            continue
        target = root / path.lstrip("/")
        if path.startswith("/usr/share/applications/") and path.endswith(".desktop"):
            result = _run(["desktop-file-validate", str(target)], runner=runner)
            validators.append({"kind": "desktop-entry", "path": path, **result})
        elif ((path.startswith("/usr/lib/systemd/system/")
               or path.startswith("/lib/systemd/system/"))
              and path.endswith((".service", ".socket", ".timer", ".path"))):
            result = _run(["systemd-analyze", "verify", str(target)], runner=runner)
            validators.append({"kind": "systemd-unit", "path": path, **result})
    return validators


def inspect_image(
    root: Path,
    *,
    include_binaries: bool = True,
    max_entries: int = MAX_IMAGE_ENTRIES,
    max_binaries: int = MAX_ELF_FILES,
    expected_machine: str | None = None,
    runner: Callable = subprocess.run,
) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if max_entries < 1 or max_entries > MAX_IMAGE_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_IMAGE_ENTRIES}")

    entries, findings, truncated = _walk_image(root, max_entries)
    inventory_bytes = json.dumps(
        entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    validators = _validate_files(root, entries, runner=runner)
    for validator in validators:
        if not validator["ok"]:
            findings.append({
                "code": "validator-failed" if validator["complete"] else "validator-incomplete",
                "message": f"{validator['kind']} validation did not pass",
                "path": validator["path"],
                "severity": "error",
            })

    binary_report = None
    if include_binaries:
        binary_report = inspect_binaries(
            root,
            expected_machine=expected_machine,
            max_files=max_binaries,
            runner=runner,
        )
        findings.extend(binary_report["findings"])

    complete = (
        not truncated
        and all(item["complete"] for item in validators)
        and (binary_report is None or binary_report["complete"])
    )
    counts: dict[str, int] = {}
    for entry in entries:
        kind = entry["type"]
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "binaries": binary_report,
        "complete": complete,
        "counts": dict(sorted(counts.items())),
        "entries": entries,
        "findings": findings,
        "inventory_sha256": hashlib.sha256(inventory_bytes).hexdigest(),
        "ok": complete and not any(item["severity"] == "error" for item in findings),
        "root": str(root),
        "truncated": truncated or bool(binary_report and binary_report["truncated"]),
        "validators": validators,
    }
