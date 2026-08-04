from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from gzh.binary_qa import (
    MAX_ELF_FILES,
    MAX_TOOL_DURATION_SECONDS,
    MAX_TOOL_OUTPUT_BYTES,
    ToolBudget,
    _run,
    inspect_binaries,
)


MAX_IMAGE_ENTRIES = 100_000
MAX_FINDING_SAMPLES_PER_CODE = 5
MAX_SUMMARY_SAMPLES = 5
MAX_VALIDATOR_SAMPLES = 5
MAX_VALIDATOR_OUTPUT_BYTES = 2048
MAX_IMAGE_TOOL_COMMANDS = MAX_ELF_FILES * 4
_ELF_MAGIC = b"\x7fELF"
_RESOURCE_COMPONENTS = frozenset({
    "assets",
    "examples",
    "fonts",
    "icons",
    "locale",
    "locales",
    "resource",
    "resources",
    "share",
    "translations",
})


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


def _normalized_image_path(value: str | Path) -> str:
    raw = str(value)
    candidate = PurePosixPath(raw if raw.startswith("/") else f"/{raw}")
    if not raw or ".." in candidate.parts or candidate == PurePosixPath("/"):
        raise ValueError(f"invalid image-relative executable path: {value}")
    return candidate.as_posix()


def _symlink_target(path: Path, root: Path) -> tuple[str, bool, bool, bool]:
    target = os.readlink(path)
    absolute = target.startswith("/")
    candidate = ((root / target.lstrip("/")) if absolute
                 else (path.parent / target))
    try:
        resolved = candidate.resolve(strict=False)
        escapes = root != resolved and root not in resolved.parents
        exists = candidate.exists()
    except (OSError, RuntimeError):
        escapes = False
        exists = False
    return target, absolute, escapes, not exists


def _classify_executable(path: Path, expected: os.stat_result) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        observed = os.fstat(stream.fileno())
        if ((observed.st_dev, observed.st_ino) != (expected.st_dev, expected.st_ino)
                or not stat.S_ISREG(observed.st_mode)):
            raise OSError("executable path changed during inspection")
        prefix = stream.read(4)
    if prefix == _ELF_MAGIC:
        return "elf"
    if prefix.startswith(b"#!"):
        return "script"
    return "unexpected-data"


def _is_resource_path(path: str) -> bool:
    return any(
        component.lower() in _RESOURCE_COMPONENTS
        for component in PurePosixPath(path).parts
    )


def _entry(
    path: Path,
    root: Path,
    *,
    executable_allowlist: set[str],
    require_non_elf_allowlist: bool,
) -> tuple[dict, list[dict]]:
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
        target, absolute, escapes, broken = _symlink_target(path, root)
        record.update({
            "target": target,
            "target_absolute": absolute,
            "target_broken": broken,
            "target_escaping": escapes,
        })
        if absolute:
            findings.append({
                "code": "absolute-symlink",
                "message": "symlink uses an absolute target",
                "path": record["path"],
                "severity": "info",
            })
        if broken:
            findings.append({
                "code": "broken-symlink",
                "message": "symlink target is absent from the inspected image",
                "path": record["path"],
                "severity": "info",
            })
        if escapes:
            findings.append({
                "code": "escaping-symlink",
                "message": "relative symlink escapes the inspected image root",
                "path": record["path"],
                "severity": "error",
            })

    if kind != "symlink" and info.st_mode & stat.S_IWOTH:
        findings.append({
            "code": "world-writable",
            "message": "installed path is world-writable",
            "path": record["path"],
            "severity": "warning",
        })
    if kind != "symlink" and info.st_mode & (stat.S_ISUID | stat.S_ISGID):
        findings.append({
            "code": "privileged-mode",
            "message": "installed path has setuid or setgid mode",
            "path": record["path"],
            "severity": "warning",
        })
    if info.st_uid != 0 or info.st_gid != 0:
        findings.append({
            "code": "owner-anomaly",
            "gid": info.st_gid,
            "message": "installed path is not owned by uid 0 and gid 0",
            "path": record["path"],
            "severity": "warning",
            "uid": info.st_uid,
        })
    if kind in {"socket", "character-device", "block-device", "unknown"}:
        findings.append({
            "code": "special-file",
            "message": f"installed image contains a {kind}",
            "path": record["path"],
            "severity": "error",
        })

    if kind == "file" and info.st_mode & 0o111:
        classification = _classify_executable(path, info)
        resource = _is_resource_path(record["path"])
        allowlisted = record["path"] in executable_allowlist
        record.update({
            "executable": classification,
            "executable_allowlisted": allowlisted,
            "resource_subtree": resource,
        })
        needs_allowlist = (
            (resource and classification != "elf")
            or (require_non_elf_allowlist and classification != "elf")
        )
        if needs_allowlist and not allowlisted:
            if resource:
                code = "unexplained-resource-executable"
                message = "resource subtree contains an unallowlisted executable regular file"
            elif classification == "script":
                code = "unallowlisted-non-elf-executable"
                message = "executable non-ELF file requires an explicit allowlist entry"
            else:
                code = "unexpected-executable"
                message = "executable regular file is neither ELF nor a script"
            findings.append({
                "classification": classification,
                "code": code,
                "message": message,
                "path": record["path"],
                "severity": "error",
            })
        elif needs_allowlist:
            findings.append({
                "classification": classification,
                "code": "allowlisted-executable",
                "message": "executable regular file is covered by the explicit allowlist",
                "path": record["path"],
                "severity": "info",
            })
        elif classification == "unexpected-data" and not allowlisted:
            findings.append({
                "classification": classification,
                "code": "unexpected-executable",
                "message": "executable regular file is neither ELF nor a script",
                "path": record["path"],
                "severity": "warning",
            })
        elif classification != "elf" and allowlisted:
            findings.append({
                "classification": classification,
                "code": "allowlisted-executable",
                "message": "executable regular file is covered by the explicit allowlist",
                "path": record["path"],
                "severity": "info",
            })
    return record, findings


def _walk_image(
    root: Path,
    limit: int,
    *,
    executable_allowlist: set[str],
    require_non_elf_allowlist: bool,
) -> tuple[list[dict], list[dict], bool, bool]:
    entries: list[dict] = []
    findings: list[dict] = []
    read_failed = False

    def onerror(exc: OSError) -> None:
        nonlocal read_failed
        read_failed = True
        failed = Path(exc.filename) if exc.filename else root
        try:
            failed_path = _relative(failed, root)
        except ValueError:
            failed_path = str(failed)
        findings.append({
            "code": "unreadable-path",
            "message": str(exc),
            "path": failed_path,
            "severity": "error",
        })

    for current, dirs, files in os.walk(
            root, followlinks=False, onerror=onerror):
        dirs.sort()
        files.sort()
        for name in [*dirs, *files]:
            if len(entries) >= limit:
                return entries, findings, True, read_failed
            path = Path(current) / name
            try:
                record, observed = _entry(
                    path,
                    root,
                    executable_allowlist=executable_allowlist,
                    require_non_elf_allowlist=require_non_elf_allowlist,
                )
            except OSError as exc:
                read_failed = True
                record = {"path": _relative(path, root), "type": "unreadable"}
                observed = [{
                    "code": "unreadable-path",
                    "message": str(exc),
                    "path": record["path"],
                    "severity": "error",
                }]
            entries.append(record)
            findings.extend(observed)
    return entries, findings, False, read_failed


def _validate_files(
    root: Path,
    entries: list[dict],
    *,
    runner: Callable,
    tool_budget: ToolBudget,
) -> tuple[list[dict], bool]:
    validators: list[dict] = []
    for entry in entries:
        path = entry["path"]
        if entry.get("type") != "file":
            continue
        target = root / path.lstrip("/")
        if path.startswith("/usr/share/applications/") and path.endswith(".desktop"):
            if tool_budget.exhausted:
                return validators, True
            result = _run(
                ["desktop-file-validate", str(target)],
                runner=runner,
                budget=tool_budget,
            )
            validators.append({"kind": "desktop-entry", "path": path, **result})
        elif ((path.startswith("/usr/lib/systemd/system/")
               or path.startswith("/lib/systemd/system/"))
              and path.endswith((".service", ".socket", ".timer", ".path"))):
            if tool_budget.exhausted:
                return validators, True
            result = _run(
                ["systemd-analyze", "verify", str(target)],
                runner=runner,
                budget=tool_budget,
            )
            validators.append({"kind": "systemd-unit", "path": path, **result})
    return validators, tool_budget.exhausted


def _finding_counts(findings: list[dict]) -> dict:
    severity = Counter(finding["severity"] for finding in findings)
    codes = Counter(finding["code"] for finding in findings)
    return {
        "by_code": dict(sorted(codes.items())),
        "by_severity": {
            name: severity.get(name, 0) for name in ("error", "warning", "info")
        },
        "total": len(findings),
    }


def _bounded_findings(findings: list[dict]) -> list[dict]:
    samples: list[dict] = []
    seen: defaultdict[str, int] = defaultdict(int)
    for finding in findings:
        code = finding["code"]
        if seen[code] >= MAX_FINDING_SAMPLES_PER_CODE:
            continue
        seen[code] += 1
        samples.append(finding)
    return samples


def _path_summary(entries: list[dict], predicate: Callable[[dict], bool]) -> dict:
    paths = [entry["path"] for entry in entries if predicate(entry)]
    return {"count": len(paths), "samples": paths[:MAX_SUMMARY_SAMPLES]}


def _image_summary(entries: list[dict]) -> dict:
    object_counts = Counter(entry["type"] for entry in entries)
    mode_counts: defaultdict[str, Counter] = defaultdict(Counter)
    executable_counts = Counter()
    for entry in entries:
        if entry.get("mode") is not None and entry["type"] != "symlink":
            mode_counts[entry["type"]][entry["mode"]] += 1
        if "executable" in entry:
            executable_counts[entry["executable"]] += 1

    return {
        "executables": {
            "allowlisted": _path_summary(
                entries, lambda item: item.get("executable_allowlisted", False)),
            "by_kind": {
                name: executable_counts.get(name, 0)
                for name in ("elf", "script", "unexpected-data")
            },
            "total": sum(executable_counts.values()),
            "unexpected": _path_summary(
                entries, lambda item: item.get("executable") == "unexpected-data"),
        },
        "mode_counts": {
            kind: dict(sorted(counts.items()))
            for kind, counts in sorted(mode_counts.items())
        },
        "object_counts": dict(sorted(object_counts.items())),
        "owner_anomalies": _path_summary(
            entries,
            lambda item: (
                "uid" in item
                and (item["uid"] != 0 or item["gid"] != 0)
            ),
        ),
        "symlinks": {
            "absolute": _path_summary(
                entries, lambda item: item.get("target_absolute", False)),
            "broken": _path_summary(
                entries, lambda item: item.get("target_broken", False)),
            "escaping": _path_summary(
                entries, lambda item: item.get("target_escaping", False)),
            "total": object_counts.get("symlink", 0),
        },
    }


def _bounded_text(value: str) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_VALIDATOR_OUTPUT_BYTES:
        return value, False
    return encoded[:MAX_VALIDATOR_OUTPUT_BYTES].decode(
        "utf-8", errors="replace"), True


def _validator_samples(validators: list[dict]) -> list[dict]:
    samples = []
    for validator in validators[:MAX_VALIDATOR_SAMPLES]:
        stdout, stdout_truncated = _bounded_text(validator["stdout"])
        stderr, stderr_truncated = _bounded_text(validator["stderr"])
        samples.append({
            "complete": validator["complete"],
            "error": validator["error"],
            "kind": validator["kind"],
            "ok": validator["ok"],
            "path": validator["path"],
            "returncode": validator["returncode"],
            "stderr": stderr,
            "stdout": stdout,
            "truncated": (
                validator["truncated"] or stdout_truncated or stderr_truncated
            ),
        })
    return samples


def _write_inventory_evidence(path: Path, payload: bytes, *, image_root: Path) -> str:
    relative = Path(path)
    if relative.is_absolute() or not relative.name or ".." in relative.parts:
        raise ValueError(
            "inventory evidence path must be relative and remain under the "
            "current directory")
    working_directory = Path.cwd().resolve()
    parent = relative.parent.resolve(strict=True)
    if parent != working_directory and working_directory not in parent.parents:
        raise ValueError("inventory evidence path must remain under the current directory")
    target = parent / relative.name
    if target == image_root or image_root in target.parents:
        raise ValueError("inventory evidence path must be outside the inspected image")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    created = True
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if target.lstat().st_uid != os.geteuid():
            raise OSError("inventory evidence file is not owned by the current user")
        directory = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        created = False
    finally:
        if created:
            target.unlink(missing_ok=True)
    return relative.as_posix()


def inspect_image(
    root: Path,
    *,
    include_binaries: bool = True,
    max_entries: int = MAX_IMAGE_ENTRIES,
    max_binaries: int = MAX_ELF_FILES,
    expected_machine: str | None = None,
    executable_allowlist: Iterable[str | Path] = (),
    require_non_elf_allowlist: bool = False,
    inventory_evidence: Path | None = None,
    runner: Callable = subprocess.run,
) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if max_entries < 1 or max_entries > MAX_IMAGE_ENTRIES:
        raise ValueError(f"max_entries must be between 1 and {MAX_IMAGE_ENTRIES}")
    allowlist = {
        _normalized_image_path(path) for path in executable_allowlist
    }
    tool_budget = ToolBudget(
        command_limit=MAX_IMAGE_TOOL_COMMANDS,
        output_limit_bytes=MAX_TOOL_OUTPUT_BYTES,
        duration_limit_seconds=MAX_TOOL_DURATION_SECONDS,
    )

    entries, findings, entry_limit_reached, image_read_failed = _walk_image(
        root,
        max_entries,
        executable_allowlist=allowlist,
        require_non_elf_allowlist=require_non_elf_allowlist,
    )
    used_allowlist = {
        entry["path"] for entry in entries
        if entry.get("executable_allowlisted", False)
    }
    for path in sorted(allowlist - used_allowlist):
        findings.append({
            "code": "unused-executable-allowlist",
            "message": "allowlist entry did not match an executable regular file",
            "path": path,
            "severity": "warning",
        })

    validators, validator_scope_truncated = _validate_files(
        root, entries, runner=runner, tool_budget=tool_budget)
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
            tool_budget=tool_budget,
        )
        for finding in binary_report["findings"]:
            observed = dict(finding)
            absolute = Path(observed["path"])
            try:
                observed["path"] = _relative(absolute, root)
            except ValueError:
                pass
            findings.append(observed)

    if tool_budget.exhausted:
        findings.append({
            "code": "tool-budget-exhausted",
            "message": "aggregate image validation tool budget was exhausted",
            "path": str(root),
            "reason": tool_budget.exhausted_reason,
            "severity": "error",
        })

    scan_complete = (
        not entry_limit_reached
        and not image_read_failed
        and not validator_scope_truncated
        and not tool_budget.exhausted
        and all(item["complete"] for item in validators)
        and (binary_report is None or binary_report["complete"])
    )
    scan_truncated = (
        entry_limit_reached
        or validator_scope_truncated
        or tool_budget.exhausted
        or bool(binary_report and binary_report["truncated"])
    )

    evidence_payload = (json.dumps({
        "schema_version": 1,
        "root": str(root),
        "complete": scan_complete,
        "truncated": scan_truncated,
        "findings": findings,
        "entries": entries,
    }, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    evidence_digest = hashlib.sha256(evidence_payload).hexdigest()
    evidence = {
        "error": None,
        "path": (
            Path(inventory_evidence).as_posix()
            if (inventory_evidence is not None
                and not Path(inventory_evidence).is_absolute()
                and ".." not in Path(inventory_evidence).parts)
            else None
        ),
        "requested": inventory_evidence is not None,
        "sha256": None,
        "written": False,
    }
    evidence_failed = False
    if require_non_elf_allowlist and inventory_evidence is None:
        evidence_failed = True
        evidence["error"] = (
            "strict executable review requires an inventory evidence path")
        findings.append({
            "code": "inventory-evidence-required",
            "message": evidence["error"],
            "path": str(root),
            "severity": "error",
        })
    if inventory_evidence is not None:
        try:
            evidence["path"] = _write_inventory_evidence(
                inventory_evidence, evidence_payload, image_root=root)
            evidence["sha256"] = evidence_digest
            evidence["written"] = True
        except (OSError, ValueError) as exc:
            evidence_failed = True
            evidence["error"] = str(exc)
            findings.append({
                "code": "inventory-write-failed",
                "message": str(exc),
                "path": str(inventory_evidence),
                "severity": "error",
            })

    complete = scan_complete and not evidence_failed
    summary = _image_summary(entries)
    counts = _finding_counts(findings)
    inline_findings = _bounded_findings(findings)
    inline_validators = _validator_samples(validators)
    binary_summary = None if binary_report is None else {
        "complete": binary_report["complete"],
        "finding_counts": _finding_counts(binary_report["findings"]),
        "ok": binary_report["ok"],
        "scanned": binary_report["scanned"],
        "truncated": binary_report["truncated"],
    }
    return {
        "findings": inline_findings,
        "finding_counts": counts,
        "findings_truncated": len(inline_findings) < len(findings),
        "summary": summary,
        "inventory": evidence,
        "binaries": binary_summary,
        "complete": complete,
        "counts": summary["object_counts"],
        "inventory_sha256": evidence_digest,
        "ok": complete and counts["by_severity"]["error"] == 0,
        "root": str(root),
        "tool_budget": tool_budget.report(),
        "truncated": scan_truncated,
        "validators": inline_validators,
        "validator_scope_complete": not validator_scope_truncated,
        "validators_truncated": (
            validator_scope_truncated or len(inline_validators) < len(validators)),
    }
