from __future__ import annotations

import os
import re
import selectors
import signal
import stat
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


MAX_ELF_FILES = 4096
MAX_DISCOVERY_FILES = 32768
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TOOL_COMMANDS = MAX_ELF_FILES * 3
MAX_TOOL_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_TOOL_DURATION_SECONDS = 15 * 60
_ELF_MAGIC = b"\x7fELF"


def _as_bytes(value: str | bytes | None) -> bytes:
    if isinstance(value, bytes):
        return value
    return (value or "").encode("utf-8", errors="replace")


def _bounded_text(
    value: str | bytes | None, limit: int = MAX_OUTPUT_BYTES,
) -> tuple[str, bool]:
    data = _as_bytes(value)
    if len(data) <= limit:
        return data.decode("utf-8", errors="replace"), False
    return data[:limit].decode("utf-8", errors="replace"), True


@dataclass
class ToolBudget:
    command_limit: int = MAX_TOOL_COMMANDS
    output_limit_bytes: int = MAX_TOOL_OUTPUT_BYTES
    duration_limit_seconds: float = MAX_TOOL_DURATION_SECONDS
    commands_started: int = 0
    output_bytes: int = 0
    exhausted_reason: str | None = None
    _started: float = field(default_factory=time.monotonic, repr=False)

    def reserve(
        self, *, timeout: float, output_limit: int,
    ) -> tuple[float | None, int | None]:
        elapsed = time.monotonic() - self._started
        if self.exhausted_reason is None and self.commands_started >= self.command_limit:
            self.exhausted_reason = "command-limit"
        if self.exhausted_reason is None and self.output_bytes >= self.output_limit_bytes:
            self.exhausted_reason = "output-limit"
        if self.exhausted_reason is None and elapsed >= self.duration_limit_seconds:
            self.exhausted_reason = "duration-limit"
        if self.exhausted_reason is not None:
            return None, None
        self.commands_started += 1
        return (
            min(timeout, max(0.001, self.duration_limit_seconds - elapsed)),
            min(output_limit, self.output_limit_bytes - self.output_bytes),
        )

    def record(self, output_bytes: int) -> None:
        self.output_bytes += output_bytes
        if self.output_bytes > self.output_limit_bytes:
            self.exhausted_reason = "output-limit"
        elif time.monotonic() - self._started > self.duration_limit_seconds:
            self.exhausted_reason = "duration-limit"

    def exhaust(self, reason: str) -> None:
        if self.exhausted_reason is None:
            self.exhausted_reason = reason

    @property
    def exhausted(self) -> bool:
        return self.exhausted_reason is not None

    def report(self) -> dict:
        return {
            "exhausted": self.exhausted,
            "reason": self.exhausted_reason,
            "limits": {
                "commands": self.command_limit,
                "duration_seconds": self.duration_limit_seconds,
                "output_bytes": self.output_limit_bytes,
            },
            "used": {
                "commands": self.commands_started,
                "duration_seconds": round(time.monotonic() - self._started, 6),
                "output_bytes": self.output_bytes,
            },
        }


class _OutputLimitExceeded(OverflowError):
    def __init__(self, stdout: bytes, stderr: bytes):
        super().__init__("tool output exceeded the limit")
        self.stdout = stdout
        self.stderr = stderr


def _stop_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait()


def _run_bounded_process(
    args: list[str], *, timeout: float, output_limit: int,
) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        _stop_process_group(process)
        raise RuntimeError("cannot capture tool output")
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    args,
                    timeout,
                    output=bytes(streams[stdout_fd]),
                    stderr=bytes(streams[stderr_fd]),
                )
            for key, _events in selector.select(min(remaining, 0.25)):
                observed = sum(len(value) for value in streams.values())
                chunk = os.read(key.fd, min(65536, output_limit - observed + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.fd].extend(chunk)
                if sum(len(value) for value in streams.values()) > output_limit:
                    raise _OutputLimitExceeded(
                        bytes(streams[stdout_fd]), bytes(streams[stderr_fd]))
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except BaseException:
        _stop_process_group(process)
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(
        args,
        returncode,
        stdout=bytes(streams[stdout_fd]),
        stderr=bytes(streams[stderr_fd]),
    )


def _tool_report(args: list[str], started: float, **values) -> dict:
    report = {
        "command": args,
        "complete": False,
        "duration_seconds": round(time.monotonic() - started, 6),
        "error": None,
        "ok": False,
        "returncode": None,
        "stderr": "",
        "stderr_bytes": 0,
        "stdout": "",
        "stdout_bytes": 0,
        "timed_out": False,
        "truncated": False,
    }
    report.update(values)
    return report


def _run(
    args: list[str],
    *,
    runner: Callable = subprocess.run,
    timeout: float = 60,
    output_limit: int = MAX_OUTPUT_BYTES,
    budget: ToolBudget | None = None,
) -> dict:
    started = time.monotonic()
    budget = budget or ToolBudget(command_limit=1)
    reserved_timeout, reserved_output = budget.reserve(
        timeout=timeout, output_limit=output_limit)
    if reserved_timeout is None or reserved_output is None:
        return _tool_report(
            args,
            started,
            error=f"aggregate-{budget.exhausted_reason}",
        )
    try:
        if runner is subprocess.run:
            proc = _run_bounded_process(
                args, timeout=reserved_timeout, output_limit=reserved_output)
        else:
            proc = runner(
                args,
                capture_output=True,
                text=True,
                timeout=reserved_timeout,
                check=False,
            )
    except FileNotFoundError:
        return _tool_report(args, started, error="tool-not-found")
    except _OutputLimitExceeded as exc:
        stdout, stderr = exc.stdout, exc.stderr
        budget.record(len(stdout) + len(stderr))
        budget.exhaust("output-limit")
        return _tool_report(
            args,
            started,
            error="output-truncated",
            stderr=stderr.decode("utf-8", errors="replace"),
            stderr_bytes=len(stderr),
            stdout=stdout.decode("utf-8", errors="replace"),
            stdout_bytes=len(stdout),
            truncated=True,
        )
    except subprocess.TimeoutExpired as exc:
        stdout_bytes = _as_bytes(exc.stdout)
        stderr_bytes = _as_bytes(exc.stderr)
        budget.record(len(stdout_bytes) + len(stderr_bytes))
        stdout, stdout_truncated = _bounded_text(stdout_bytes, reserved_output)
        stderr_limit = max(0, reserved_output - len(stdout.encode()))
        stderr, stderr_truncated = _bounded_text(stderr_bytes, stderr_limit)
        return _tool_report(
            args,
            started,
            error="timeout",
            stderr=stderr,
            stderr_bytes=len(stderr_bytes),
            stdout=stdout,
            stdout_bytes=len(stdout_bytes),
            timed_out=True,
            truncated=stdout_truncated or stderr_truncated,
        )
    raw_stdout = _as_bytes(proc.stdout)
    raw_stderr = _as_bytes(proc.stderr)
    budget.record(len(raw_stdout) + len(raw_stderr))
    stdout, stdout_truncated = _bounded_text(raw_stdout, reserved_output)
    stderr_limit = max(0, reserved_output - len(stdout.encode()))
    stderr, stderr_truncated = _bounded_text(raw_stderr, stderr_limit)
    truncated = stdout_truncated or stderr_truncated
    if truncated:
        budget.exhaust("output-limit")
    aggregate_exhausted = budget.exhausted and not truncated
    return _tool_report(
        args,
        started,
        complete=not truncated and not aggregate_exhausted,
        error=("output-truncated" if truncated else
               f"aggregate-{budget.exhausted_reason}"
               if aggregate_exhausted else None),
        ok=proc.returncode == 0 and not truncated and not aggregate_exhausted,
        returncode=proc.returncode,
        stderr=stderr,
        stderr_bytes=len(raw_stderr),
        stdout=stdout,
        stdout_bytes=len(raw_stdout),
        truncated=truncated,
    )


def _container_format(header: bytes) -> str | None:
    if header.startswith(_ELF_MAGIC) and header[8:11] in {b"AI\x01", b"AI\x02"}:
        return "appimage"
    if header.startswith(b"PK\x03\x04"):
        return "zip"
    if header.startswith((b"hsqs", b"sqsh")):
        return "squashfs"
    if header.startswith(b"!<arch>\n"):
        return "ar"
    if len(header) >= 262 and header[257:262] == b"ustar":
        return "tar"
    return None


def _probe_regular_file(path: Path) -> tuple[bool, str | None, str | None]:
    try:
        expected = path.lstat()
        if not stat.S_ISREG(expected.st_mode):
            return False, None, None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            observed = os.fstat(stream.fileno())
            if ((expected.st_dev, expected.st_ino) !=
                    (observed.st_dev, observed.st_ino)):
                raise OSError("file changed during inspection")
            header = stream.read(512)
    except OSError as exc:
        return False, None, str(exc)
    return header.startswith(_ELF_MAGIC), _container_format(header), None


def _binary_paths(
    target: Path, limit: int,
) -> tuple[list[Path], list[dict], list[dict], bool, bool]:
    if target.is_file():
        is_elf, container, error = _probe_regular_file(target)
        nested = ([{"format": container, "path": str(target)}]
                  if container is not None else [])
        unreadable = ([{"error": error, "path": str(target)}]
                      if error is not None else [])
        return ([target] if is_elf else []), nested, unreadable, False, False
    paths: list[Path] = []
    nested: list[dict] = []
    unreadable: list[dict] = []
    truncated = False
    discovery_truncated = False
    discovered = 0
    for root, dirs, files in os.walk(target, followlinks=False):
        dirs.sort()
        files.sort()
        for name in files:
            path = Path(root) / name
            if path.is_symlink():
                continue
            discovered += 1
            if discovered > MAX_DISCOVERY_FILES:
                discovery_truncated = True
                return paths, nested, unreadable, truncated, discovery_truncated
            is_elf, container, error = _probe_regular_file(path)
            if error is not None:
                unreadable.append({"error": error, "path": str(path)})
                continue
            if container is not None:
                nested.append({"format": container, "path": str(path)})
            if not is_elf:
                continue
            if len(paths) >= limit:
                truncated = True
                return paths, nested, unreadable, truncated, discovery_truncated
            paths.append(path)
    return paths, nested, unreadable, truncated, discovery_truncated


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


def _runtime_dependencies(text: str) -> tuple[list[dict], list[str]]:
    dependencies: list[dict] = []
    unresolved: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\S+)\s+=>\s+(.+?)\s*$", line)
        if match is None:
            continue
        name, resolved = match.groups()
        missing = resolved.casefold() in {"none", "not found"}
        dependencies.append({
            "name": name,
            "path": None if missing else resolved,
            "resolved": not missing,
            "root": not line[:1].isspace(),
        })
        if missing:
            unresolved.append(name)
    return dependencies, unresolved


def inspect_elf(
    path: Path,
    *,
    expected_machine: str | None = None,
    runner: Callable = subprocess.run,
    tool_budget: ToolBudget | None = None,
) -> dict:
    path = Path(path).resolve()
    budget = tool_budget or ToolBudget(command_limit=3)
    is_elf, _container, read_error = _probe_regular_file(path)
    if not path.is_file() or not is_elf:
        message = (f"target could not be read: {read_error}"
                   if read_error else "target is not a regular ELF file")
        return {
            "complete": read_error is None,
            "findings": [{
                "code": "unreadable-file" if read_error else "not-elf",
                "severity": "error",
                "message": message,
            }],
            "ok": False,
            "path": str(path),
            "tool_budget": budget.report(),
            "tools": [],
            "truncated": False,
        }

    file_report = _run(
        ["file", "--brief", "--dereference", str(path)],
        runner=runner,
        budget=budget,
    )
    readelf_report = _run(
        ["readelf", "--file-header", "--program-headers", "--dynamic", "--wide", str(path)],
        runner=runner,
        budget=budget,
    )
    lddtree_report = _run(
        ["lddtree", str(path)], runner=runner, budget=budget)
    tools = [file_report, readelf_report, lddtree_report]
    text = readelf_report["stdout"] if readelf_report["ok"] else ""
    header = {
        "class": _header_value(text, "Class"),
        "data": _header_value(text, "Data"),
        "machine": _header_value(text, "Machine"),
        "type": _header_value(text, "Type"),
    }
    missing_fields = [name for name, value in header.items() if not value]
    file_output_valid = bool(
        file_report["ok"]
        and re.search(r"\bELF\b", file_report["stdout"]))
    readelf_output_valid = bool(
        readelf_report["ok"]
        and "ELF Header:" in text
        and not missing_fields
        and header["class"] in {"ELF32", "ELF64"})
    machine = header["machine"]
    interpreter = _program_interpreter(text)
    findings = _program_header_findings(text)
    needed = _dynamic_values(text, "NEEDED")
    runtime_dependencies, unresolved = _runtime_dependencies(
        lddtree_report["stdout"] if lddtree_report["ok"] else "")
    lddtree_output_valid = bool(
        lddtree_report["ok"]
        and lddtree_report["stdout"].strip()
        and runtime_dependencies)
    resolution_by_name: dict[str, list[dict]] = {}
    for dependency in runtime_dependencies:
        if dependency["root"]:
            continue
        resolution_by_name.setdefault(dependency["name"], []).append(dependency)
    missing_resolution = [
        dependency for dependency in needed
        if (dependency not in resolution_by_name
            or not any(item["resolved"] for item in resolution_by_name[dependency]))
    ]
    if not file_output_valid:
        findings.append({
            "code": "malformed-file-output",
            "severity": "error",
            "message": "file succeeded without recognizable ELF evidence",
        })
    if not readelf_output_valid:
        findings.append({
            "code": "malformed-readelf-output",
            "missing_fields": missing_fields,
            "severity": "error",
            "message": "readelf succeeded without the required ELF header fields",
        })
    if not lddtree_output_valid:
        findings.append({
            "code": "malformed-lddtree-output",
            "severity": "error",
            "message": "lddtree succeeded without a parseable dependency tree",
        })
    for dependency in unresolved:
        findings.append({
            "code": "unresolved-needed",
            "dependency": dependency,
            "severity": "error",
            "message": "runtime dependency was not resolved by lddtree",
        })
    for dependency in missing_resolution:
        if dependency in unresolved:
            continue
        findings.append({
            "code": "missing-runtime-resolution",
            "dependency": dependency,
            "severity": "error",
            "message": "DT_NEEDED entry is absent from resolved lddtree evidence",
        })
    if interpreter and not Path(interpreter).exists():
        findings.append({
            "code": "missing-interpreter",
            "interpreter": interpreter,
            "severity": "error",
            "message": "ELF interpreter is not present in the host-visible filesystem",
        })
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
    runtime_complete = (
        lddtree_output_valid and not unresolved and not missing_resolution)
    complete = (
        all(item["complete"] and item["ok"] for item in tools)
        and file_output_valid
        and readelf_output_valid
        and runtime_complete
        and not budget.exhausted
    )
    if not complete:
        findings.append({
            "code": "incomplete-tool-evidence",
            "severity": "error",
            "message": "file, readelf, or lddtree evidence is incomplete",
        })

    return {
        "complete": complete,
        "elf": {
            "class": header["class"],
            "data": header["data"],
            "interpreter": interpreter,
            "machine": machine,
            "needed": needed,
            "rpath": rpath,
            "runpath": _dynamic_values(text, "RUNPATH"),
            "soname": _dynamic_values(text, "SONAME"),
            "type": header["type"],
        },
        "runtime_dependency_resolution": {
            "complete": runtime_complete,
            "dependencies": runtime_dependencies,
            "missing_needed": missing_resolution,
            "scope": "host-visible filesystem and ELF loader search paths",
            "unresolved": unresolved,
        },
        "file": file_report["stdout"].strip(),
        "findings": findings,
        "ok": complete and not any(item["severity"] == "error" for item in findings),
        "path": str(path),
        "tool_budget": budget.report(),
        "tools": tools,
        "truncated": any(item["truncated"] for item in tools),
    }


def inspect_binaries(
    target: Path,
    *,
    expected_machine: str | None = None,
    max_files: int = MAX_ELF_FILES,
    runner: Callable = subprocess.run,
    tool_budget: ToolBudget | None = None,
) -> dict:
    target = Path(target).resolve()
    if max_files < 1 or max_files > MAX_ELF_FILES:
        raise ValueError(f"max_files must be between 1 and {MAX_ELF_FILES}")
    if not target.exists():
        raise FileNotFoundError(target)
    budget = tool_budget or ToolBudget(command_limit=max_files * 3)
    paths, nested, unreadable, truncated, discovery_truncated = _binary_paths(
        target, max_files)
    reports = []
    tool_scope_truncated = False
    for path in paths:
        if budget.exhausted:
            tool_scope_truncated = True
            break
        reports.append(inspect_elf(
            path,
            expected_machine=expected_machine,
            runner=runner,
            tool_budget=budget,
        ))
        if budget.exhausted:
            tool_scope_truncated = len(reports) < len(paths)
            break
    findings = [
        {"path": report["path"], **finding}
        for report in reports
        for finding in report["findings"]
    ]
    for item in nested:
        findings.append({
            "code": "nested-scope-unreviewed",
            "format": item["format"],
            "message": "nested container content was detected but not traversed",
            "path": item["path"],
            "severity": "error",
        })
    for item in unreadable:
        findings.append({
            "code": "unreadable-file",
            "message": item["error"],
            "path": item["path"],
            "severity": "error",
        })
    if budget.exhausted:
        findings.append({
            "code": "tool-budget-exhausted",
            "message": "aggregate binary inspection tool budget was exhausted",
            "reason": budget.exhausted_reason,
            "path": str(target),
            "severity": "error",
        })
    unsupported_target = (
        not target.is_dir() and not paths and not nested and not unreadable)
    if unsupported_target:
        findings.append({
            "code": "unsupported-binary-target",
            "message": "target is neither ELF nor a recognized nested container",
            "path": str(target),
            "severity": "error",
        })
    complete = (
        not truncated
        and not discovery_truncated
        and not tool_scope_truncated
        and not nested
        and not unreadable
        and not unsupported_target
        and not budget.exhausted
        and len(reports) == len(paths)
        and all(report["complete"] for report in reports)
    )
    return {
        "complete": complete,
        "files": reports,
        "findings": findings,
        "nested_containers": nested,
        "ok": (complete and all(report["ok"] for report in reports)
               and not any(item["severity"] == "error" for item in findings)),
        "scope": {
            "elf_metadata": "complete" if complete else "incomplete",
            "nested_containers": "detected-not-traversed" if nested else "none-detected",
            "runtime_dependencies": "lddtree-host-visible",
            "runtime_provider_declarations": "not-reviewed",
        },
        "scanned": len(reports),
        "target": str(target),
        "tool_budget": budget.report(),
        "truncated": (truncated or discovery_truncated or tool_scope_truncated
                      or budget.exhausted
                      or any(report["truncated"] for report in reports)),
    }
