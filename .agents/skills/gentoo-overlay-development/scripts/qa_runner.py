#!/usr/bin/env python3
"""Run a bounded, read-only pkgcheck scan with structured provenance."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1
SEVERITIES = ("error", "warning", "style", "info")
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TIMEOUT = 900
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 256 * 1024
ADAPTER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
CANONICAL_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,98}[A-Za-z0-9])?")
REPO_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
TARGET_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9+_.-]*/"
    r"[A-Za-z0-9][A-Za-z0-9+_.-]*")
REVISION_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SCRIPT_ROOT = Path(__file__).resolve().parent
SOURCE_MANAGER_PATH = SCRIPT_ROOT / "source_manager.py"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def structured_error(stage: str, exc: Exception) -> dict:
    return {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
    }


def load_source_manager():
    spec = importlib.util.spec_from_file_location(
        "gentoo_overlay_source_manager", SOURCE_MANAGER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load shared source manager")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pkgcheck_evidence() -> dict:
    manager = load_source_manager()
    registry = manager.load_registry()
    lock = manager.load_lock()
    matches = [source for source in registry["sources"]
               if source["id"] == "pkgcheck"]
    if len(matches) != 1:
        raise ValueError("source registry must contain exactly one pkgcheck entry")
    source = matches[0]
    reviewed = lock["sources"].get("pkgcheck")
    if (source["authority"] != "gentoo-tool"
            or source.get("scope") != "portable-core"
            or not source["url"].startswith("https://")
            or not isinstance(reviewed, dict)
            or not SHA256_RE.fullmatch(reviewed.get("sha256", ""))
            or not reviewed.get("checked_at")):
        raise ValueError("pkgcheck evidence lock is incomplete")
    return {
        "source": source,
        "reviewed_lock": reviewed,
        "registry_schema": registry["schema"],
        "lock_schema": lock["schema"],
        "lock_updated_at": lock.get("updated_at"),
        "source_network_audit_performed": False,
    }


def validate_identifier(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


def validate_target(target: str | None) -> str | None:
    if target is None:
        return None
    if len(target) > 256 or not TARGET_RE.fullmatch(target):
        raise ValueError(
            "target must be one bounded category/package atom without options")
    return target


def path_is_within(path: Path, directory: Path) -> bool:
    resolved_path = path.expanduser().resolve()
    resolved_directory = directory.expanduser().resolve()
    return (resolved_path == resolved_directory
            or resolved_path.is_relative_to(resolved_directory))


def parse_layout(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise ValueError("metadata/layout.conf is missing")
    content = path.read_text(encoding="utf-8")
    if len(content.encode()) > 64 * 1024:
        raise ValueError("metadata/layout.conf exceeds 65536 bytes")
    result: dict[str, str] = {}
    for number, raw_line in enumerate(content.splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (not separator or not re.fullmatch(r"[A-Za-z0-9_-]+", key)
                or not value or key in result):
            raise ValueError(
                f"invalid metadata/layout.conf assignment on line {number}")
        result[key] = value
    if not result:
        raise ValueError("metadata/layout.conf has no assignments")
    return result


def run_git(repository: Path, *arguments: str) -> subprocess.CompletedProcess:
    proc, _duration, _stdout_bytes, _stderr_bytes = bounded_process(
        ["git", "-C", str(repository), *arguments], None, 30,
        MAX_GIT_OUTPUT_BYTES)
    return proc


def git_output(repository: Path, *arguments: str, optional: bool = False) -> str | None:
    proc = run_git(repository, *arguments)
    if proc.returncode != 0:
        if optional:
            return None
        raise ValueError(
            proc.stderr.strip() or f"git {' '.join(arguments)} failed")
    return proc.stdout.strip()


def repository_provenance(path: Path) -> dict:
    repository = path.expanduser().resolve()
    if not repository.is_dir():
        raise ValueError("repository path is not a directory")
    repo_name_path = repository / "profiles" / "repo_name"
    layout_path = repository / "metadata" / "layout.conf"
    if not repo_name_path.is_file():
        raise ValueError("profiles/repo_name is missing")
    repo_name = repo_name_path.read_text(encoding="utf-8").strip()
    validate_identifier(repo_name, REPO_NAME_RE, "profiles/repo_name")
    layout = parse_layout(layout_path)
    top_level = git_output(repository, "rev-parse", "--show-toplevel")
    if Path(top_level).resolve() != repository:
        raise ValueError("repository path is not the Git worktree root")
    revision = git_output(repository, "rev-parse", "HEAD")
    if not REVISION_RE.fullmatch(revision or ""):
        raise ValueError("Git HEAD is not an immutable revision")
    branch = git_output(
        repository, "symbolic-ref", "--quiet", "--short", "HEAD",
        optional=True)
    status = git_output(
        repository, "status", "--porcelain=v1", "--untracked-files=normal")
    origin = git_output(
        repository, "remote", "get-url", "origin", optional=True)
    return {
        "root": str(repository),
        "repo_name": repo_name,
        "layout": layout,
        "layout_sha256": hashlib.sha256(
            layout_path.read_bytes()).hexdigest(),
        "git_revision": revision,
        "git_branch": branch,
        "git_detached": branch is None,
        "git_dirty": bool(status),
        "git_status": status.splitlines() if status else [],
        "configured_origin": origin,
        "configured_origin_state": "configured" if origin else "missing",
    }


def parse_findings(stdout: str) -> list[dict]:
    findings = []
    for number, raw_line in enumerate(stdout.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            finding = json.loads(line, parse_constant=reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"pkgcheck JsonStream line {number} is invalid JSON") from exc
        if (not isinstance(finding, dict)
                or not isinstance(finding.get("__class__"), str)
                or not finding["__class__"].strip()):
            raise ValueError(
                f"pkgcheck JsonStream line {number} is not a finding object")
        findings.append({**finding, "code": finding["__class__"]})
    return findings


def command_result(
        command: list[str], repository: Path | None, timeout: int,
        maximum: int,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> tuple[subprocess.CompletedProcess, float, int, int]:
    if runner is None:
        return bounded_process(command, repository, timeout, maximum)
    started = time.monotonic()
    proc = runner(
        command, cwd=str(repository) if repository else None,
        capture_output=True, text=True,
        timeout=timeout)
    duration = round(time.monotonic() - started, 3)
    stdout_bytes = len(output_text(proc.stdout).encode())
    stderr_bytes = len(output_text(proc.stderr).encode())
    if stdout_bytes + stderr_bytes > maximum:
        raise OverflowError(
            f"pkgcheck output exceeds {maximum} bytes")
    return proc, duration, stdout_bytes, stderr_bytes


def stop_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def bounded_process(command: list[str], repository: Path | None,
                    timeout: int, maximum: int,
                    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
                    ) -> tuple[subprocess.CompletedProcess, float, int, int]:
    started = time.monotonic()
    proc = popen(
        command, cwd=str(repository) if repository else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    if proc.stdout is None or proc.stderr is None:
        stop_process_group(proc)
        raise RuntimeError("cannot capture subprocess output")
    stdout_fd = proc.stdout.fileno()
    stderr_fd = proc.stderr.fileno()
    streams = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    selector.register(proc.stderr, selectors.EVENT_READ)
    deadline = started + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(
                    command, timeout, output=bytes(streams[stdout_fd]),
                    stderr=bytes(streams[stderr_fd]))
            for key, _events in selector.select(min(remaining, 0.25)):
                current_size = sum(len(value) for value in streams.values())
                chunk = os.read(key.fd, min(65536, maximum - current_size + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.fd].extend(chunk)
                if sum(len(value) for value in streams.values()) > maximum:
                    raise OverflowError(
                        f"subprocess output exceeds {maximum} bytes")
        returncode = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except Exception:
        stop_process_group(proc)
        raise
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    duration = round(time.monotonic() - started, 3)
    stdout = bytes(streams[stdout_fd]).decode(errors="replace")
    stderr = bytes(streams[stderr_fd]).decode(errors="replace")
    return (subprocess.CompletedProcess(
        command, returncode, stdout=stdout, stderr=stderr), duration,
        len(stdout.encode()), len(stderr.encode()))


def base_report(adapter_id: str, canonical_repository: str,
                severity: str, target: str | None, net: bool,
                timeout: int, maximum: int) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "ok": False,
        "complete": False,
        "truncated": False,
        "scope": {
            "adapter_id": adapter_id,
            "adapter_identity_state": "configured-unverified",
            "canonical_repository": canonical_repository,
            "canonical_repository_identity_state": "configured-unverified",
            "severity_threshold": severity,
            "target": target,
            "network_enabled": net,
        },
        "repository": None,
        "tool": {
            "name": "pkgcheck",
            "executable": None,
            "version": None,
            "command": None,
            "timeout_seconds": timeout,
            "max_output_bytes": maximum,
        },
        "official_evidence": None,
        "network_checks": {
            "requested": net,
            "attempted": False,
            "completed": False,
        },
        "execution": None,
        "findings": [],
        "errors": [],
    }


def collect(repository_path: Path, adapter_id: str,
            canonical_repository: str, *, severity: str = "warning",
            target: str | None = None, net: bool = False,
            timeout: int = DEFAULT_TIMEOUT,
            max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
            output_path: Path | None = None,
            runner: Callable[..., subprocess.CompletedProcess] | None = None,
            which: Callable[[str], str | None] = shutil.which) -> dict:
    report = base_report(
        adapter_id, canonical_repository, severity, target, net, timeout,
        max_output_bytes)
    stage = "input"
    try:
        if output_path is not None and path_is_within(
                output_path, repository_path):
            raise ValueError("output path must be outside the target repository")
        validate_identifier(adapter_id, ADAPTER_RE, "adapter id")
        validate_identifier(
            canonical_repository, CANONICAL_REPOSITORY_RE,
            "canonical repository identity")
        if severity not in SEVERITIES:
            raise ValueError(f"invalid severity threshold: {severity}")
        target = validate_target(target)
        if not 1 <= timeout <= MAX_TIMEOUT:
            raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
        if not 256 <= max_output_bytes <= MAX_OUTPUT_BYTES:
            raise ValueError(
                f"max output must be between 256 and {MAX_OUTPUT_BYTES} bytes")
        stage = "repository"
        repository = repository_provenance(repository_path)
        root = Path(repository["root"])
        report["repository"] = repository
        if repository["git_dirty"]:
            raise ValueError(
                "target repository worktree must be clean before pkgcheck")
        stage = "evidence"
        report["official_evidence"] = pkgcheck_evidence()
        stage = "tool"
        executable = which("pkgcheck")
        if not executable:
            raise FileNotFoundError("pkgcheck executable was not found")
        executable_path = str(Path(executable).absolute())
        report["tool"]["executable"] = executable_path
        version_proc, _version_duration, _version_stdout, _version_stderr = (
            command_result(
                [executable_path, "--version"], None, min(timeout, 30),
                min(max_output_bytes, 1024), runner))
        version = output_text(version_proc.stdout).strip()
        if (version_proc.returncode != 0 or not version
                or len(version.encode()) > 1024):
            raise RuntimeError("cannot obtain a bounded pkgcheck version")
        report["tool"]["version"] = version

        before_status = repository["git_status"]
        stage = "pkgcheck"
        with tempfile.TemporaryDirectory(prefix="gentoo-qa-pkgcheck-") as cache:
            command = [
                executable_path, "scan", "-R", "JsonStream", "--exit",
                severity, "--repo", str(root), "--color", "false",
                "--cache-dir", cache,
            ]
            if net:
                command.append("--net")
            if target:
                command.append(target)
            report["tool"]["command"] = command
            report["network_checks"]["attempted"] = net
            proc, duration, stdout_bytes, stderr_bytes = command_result(
                command, root, timeout, max_output_bytes, runner)
        stdout = output_text(proc.stdout)
        stderr = output_text(proc.stderr)
        findings = parse_findings(stdout)
        after_status = git_output(
            root, "status", "--porcelain=v1", "--untracked-files=normal")
        after_lines = after_status.splitlines() if after_status else []
        if after_lines != before_status:
            raise RuntimeError("pkgcheck changed the target repository worktree")
        gate_complete = proc.returncode == 0 or (
            proc.returncode == 1 and bool(findings))
        report["network_checks"]["completed"] = net and gate_complete
        report["execution"] = {
            "returncode": proc.returncode,
            "duration_seconds": duration,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "stderr": stderr,
        }
        report["findings"] = findings
        if proc.returncode == 0:
            report["ok"] = True
            report["complete"] = True
        elif gate_complete:
            report["complete"] = True
        else:
            raise RuntimeError(
                f"pkgcheck failed without a complete finding stream "
                f"(returncode {proc.returncode})")
    except subprocess.TimeoutExpired as exc:
        report["truncated"] = True
        report["errors"].append(structured_error(
            "pkgcheck", TimeoutError(
                f"pkgcheck timed out after {exc.timeout} seconds")))
    except OverflowError as exc:
        report["truncated"] = True
        report["errors"].append(structured_error(f"{stage}-output", exc))
    except (OSError, RuntimeError, ValueError) as exc:
        report["errors"].append(structured_error(stage, exc))
    return report


def atomic_write(path: Path, payload: str) -> None:
    destination = path.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, destination)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def bounded_integer(minimum: int, maximum: int):
    def parse(value: str) -> int:
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"must be between {minimum} and {maximum}")
        return number
    return parse


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository", required=True, type=Path)
    result.add_argument("--adapter-id", required=True)
    result.add_argument("--canonical-repository", required=True)
    result.add_argument("--severity", choices=SEVERITIES, default="warning")
    result.add_argument("--target")
    result.add_argument("--net", action="store_true")
    result.add_argument(
        "--timeout", type=bounded_integer(1, MAX_TIMEOUT),
        default=DEFAULT_TIMEOUT)
    result.add_argument(
        "--max-output-bytes", type=bounded_integer(256, MAX_OUTPUT_BYTES),
        default=DEFAULT_MAX_OUTPUT_BYTES)
    result.add_argument("--output", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = collect(
        args.repository, args.adapter_id, args.canonical_repository,
        severity=args.severity, target=args.target, net=args.net,
        timeout=args.timeout, max_output_bytes=args.max_output_bytes,
        output_path=args.output)
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    wrote_output = False
    if args.output and not path_is_within(args.output, args.repository):
        try:
            atomic_write(args.output, payload)
            wrote_output = True
        except OSError as exc:
            report["ok"] = False
            report["complete"] = False
            report["errors"].append(structured_error("output", exc))
            payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if wrote_output:
        state = "complete" if report["complete"] and not report["truncated"] else "incomplete"
        print(f"Wrote {state} QA report to {args.output.expanduser().resolve()}.")
    else:
        print(payload, end="")
    return int(not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
