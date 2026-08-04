from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable


MAX_ELF_FILES = 4096
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
_ELF_MAGIC = b"\x7fELF"


def _bounded_text(value: str | None, limit: int = MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    data = (value or "").encode("utf-8", errors="replace")
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    return data[:limit].decode("utf-8", errors="replace"), True


def _run(
    args: list[str],
    *,
    runner: Callable = subprocess.run,
    timeout: int = 60,
) -> dict:
    started = time.monotonic()
    try:
        proc = runner(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return {
            "command": args,
            "complete": False,
            "duration_seconds": round(time.monotonic() - started, 6),
            "error": "tool-not-found",
            "ok": False,
            "returncode": None,
            "stderr": "",
            "stdout": "",
            "timed_out": False,
            "truncated": False,
        }
    except subprocess.TimeoutExpired as exc:
        stdout, stdout_truncated = _bounded_text(exc.stdout)
        stderr, stderr_truncated = _bounded_text(exc.stderr)
        return {
            "command": args,
            "complete": False,
            "duration_seconds": round(time.monotonic() - started, 6),
            "error": "timeout",
            "ok": False,
            "returncode": None,
            "stderr": stderr,
            "stdout": stdout,
            "timed_out": True,
            "truncated": stdout_truncated or stderr_truncated,
        }
    stdout, stdout_truncated = _bounded_text(proc.stdout)
    stderr, stderr_truncated = _bounded_text(proc.stderr)
    truncated = stdout_truncated or stderr_truncated
    return {
        "command": args,
        "complete": not truncated,
        "duration_seconds": round(time.monotonic() - started, 6),
        "error": "output-truncated" if truncated else None,
        "ok": proc.returncode == 0 and not truncated,
        "returncode": proc.returncode,
        "stderr": stderr,
        "stdout": stdout,
        "timed_out": False,
        "truncated": truncated,
    }


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == _ELF_MAGIC
    except OSError:
        return False


def _elf_paths(target: Path, limit: int) -> tuple[list[Path], bool]:
    if target.is_file():
        return ([target] if _is_elf(target) else []), False
    paths: list[Path] = []
    truncated = False
    for root, dirs, files in os.walk(target, followlinks=False):
        dirs.sort()
        files.sort()
        for name in files:
            path = Path(root) / name
            if path.is_symlink() or not _is_elf(path):
                continue
            if len(paths) >= limit:
                truncated = True
                return paths, truncated
            paths.append(path)
    return paths, truncated


def _header_value(text: str, label: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(label)}:\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1) if match else None


def _dynamic_values(text: str, tag: str) -> list[str]:
    values = []
    for line in text.splitlines():
        if f"({tag})" not in line:
            continue
        match = re.search(r"\[(.*?)\]", line)
        values.append(match.group(1) if match else line.strip())
    return values


def _program_interpreter(text: str) -> str | None:
    match = re.search(r"Requesting program interpreter:\s*([^\]]+)\]", text)
    return match.group(1).strip() if match else None


def _program_header_findings(text: str) -> list[dict]:
    findings: list[dict] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("GNU_STACK") and re.search(r"\bRWE\b", stripped):
            findings.append({
                "code": "executable-stack",
                "severity": "error",
                "message": "GNU_STACK is executable",
            })
        if stripped.startswith("LOAD") and re.search(r"\bRWE\b", stripped):
            findings.append({
                "code": "writable-executable-segment",
                "severity": "error",
                "message": "a LOAD segment is both writable and executable",
            })
    return findings


def inspect_elf(
    path: Path,
    *,
    expected_machine: str | None = None,
    runner: Callable = subprocess.run,
) -> dict:
    path = Path(path).resolve()
    if not path.is_file() or not _is_elf(path):
        return {
            "complete": True,
            "findings": [{
                "code": "not-elf",
                "severity": "error",
                "message": "target is not a regular ELF file",
            }],
            "ok": False,
            "path": str(path),
            "tools": [],
            "truncated": False,
        }

    file_report = _run(["file", "--brief", "--dereference", str(path)], runner=runner)
    readelf_report = _run(
        ["readelf", "--file-header", "--program-headers", "--dynamic", "--wide", str(path)],
        runner=runner,
    )
    tools = [file_report, readelf_report]
    complete = all(item["complete"] and item["ok"] for item in tools)
    text = readelf_report["stdout"] if readelf_report["ok"] else ""
    machine = _header_value(text, "Machine")
    findings = _program_header_findings(text)
    if "(TEXTREL)" in text or re.search(r"\bTEXTREL\b", text):
        findings.append({
            "code": "text-relocations",
            "severity": "error",
            "message": "dynamic metadata declares text relocations",
        })
    rpath = _dynamic_values(text, "RPATH")
    if rpath:
        findings.append({
            "code": "rpath",
            "severity": "warning",
            "message": "DT_RPATH is present",
            "values": rpath,
        })
    if expected_machine and machine != expected_machine:
        findings.append({
            "code": "unexpected-machine",
            "severity": "error",
            "expected": expected_machine,
            "observed": machine,
            "message": "ELF machine does not match the requested architecture",
        })
    if not complete:
        findings.append({
            "code": "incomplete-tool-evidence",
            "severity": "error",
            "message": "file or readelf evidence is incomplete",
        })

    return {
        "complete": complete,
        "elf": {
            "class": _header_value(text, "Class"),
            "data": _header_value(text, "Data"),
            "interpreter": _program_interpreter(text),
            "machine": machine,
            "needed": _dynamic_values(text, "NEEDED"),
            "rpath": rpath,
            "runpath": _dynamic_values(text, "RUNPATH"),
            "soname": _dynamic_values(text, "SONAME"),
            "type": _header_value(text, "Type"),
        },
        "file": file_report["stdout"].strip(),
        "findings": findings,
        "ok": complete and not any(item["severity"] == "error" for item in findings),
        "path": str(path),
        "tools": tools,
        "truncated": any(item["truncated"] for item in tools),
    }


def inspect_binaries(
    target: Path,
    *,
    expected_machine: str | None = None,
    max_files: int = MAX_ELF_FILES,
    runner: Callable = subprocess.run,
) -> dict:
    target = Path(target).resolve()
    if max_files < 1 or max_files > MAX_ELF_FILES:
        raise ValueError(f"max_files must be between 1 and {MAX_ELF_FILES}")
    if not target.exists():
        raise FileNotFoundError(target)
    paths, truncated = _elf_paths(target, max_files)
    reports = [
        inspect_elf(path, expected_machine=expected_machine, runner=runner)
        for path in paths
    ]
    complete = not truncated and all(report["complete"] for report in reports)
    return {
        "complete": complete,
        "files": reports,
        "findings": [
            {"path": report["path"], **finding}
            for report in reports
            for finding in report["findings"]
        ],
        "ok": complete and all(report["ok"] for report in reports),
        "scanned": len(reports),
        "target": str(target),
        "truncated": truncated or any(report["truncated"] for report in reports),
    }
