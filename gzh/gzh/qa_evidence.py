from __future__ import annotations

import hashlib
import os
import selectors
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence


DEFAULT_TIMEOUT = 300
DEFAULT_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TIMEOUT = 24 * 60 * 60
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


def output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _stop_process_group(proc: subprocess.Popen) -> None:
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


class OutputLimitExceeded(OverflowError):
    def __init__(self, maximum: int, stdout: bytes, stderr: bytes):
        super().__init__(f"command output exceeds {maximum} bytes")
        self.stdout = stdout
        self.stderr = stderr


def _bounded_process(
        command: Sequence[str], cwd: Path | None, timeout: int, maximum: int,
        env: Mapping[str, str] | None = None,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> tuple[subprocess.CompletedProcess, float, int, int]:
    started = time.monotonic()
    proc = popen(
        list(command), cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(env) if env is not None else None,
        start_new_session=True)
    if proc.stdout is None or proc.stderr is None:
        _stop_process_group(proc)
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
                    list(command), timeout, output=bytes(streams[stdout_fd]),
                    stderr=bytes(streams[stderr_fd]))
            for key, _events in selector.select(min(remaining, 0.25)):
                current_size = sum(len(value) for value in streams.values())
                chunk = os.read(key.fd, min(65536, maximum - current_size + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.fd].extend(chunk)
                if sum(len(value) for value in streams.values()) > maximum:
                    raise OutputLimitExceeded(
                        maximum, bytes(streams[stdout_fd]),
                        bytes(streams[stderr_fd]))
        returncode = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
    except Exception:
        _stop_process_group(proc)
        raise
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    duration = round(time.monotonic() - started, 3)
    stdout = bytes(streams[stdout_fd]).decode(errors="replace")
    stderr = bytes(streams[stderr_fd]).decode(errors="replace")
    return (subprocess.CompletedProcess(
        list(command), returncode, stdout=stdout, stderr=stderr), duration,
        len(stdout.encode()), len(stderr.encode()))


def _bounded_prefix(stdout: bytes, stderr: bytes, maximum: int) -> tuple[bytes, bytes]:
    stdout = stdout[:maximum]
    stderr = stderr[:max(0, maximum - len(stdout))]
    return stdout, stderr


def run_evidence_command(
        command: Sequence[str], *, cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    """Run one command with the bounded execution semantics used by generic QA."""
    args = [str(value) for value in command]
    report = {
        "command": args,
        "cwd": str(cwd.resolve()) if cwd else None,
        "returncode": None,
        "duration_seconds": None,
        "stdout": "",
        "stderr": "",
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "timed_out": False,
        "complete": False,
        "truncated": False,
        "skipped": False,
        "state": "incomplete",
        "error": None,
    }
    if not args:
        report["error"] = {"type": "ValueError", "message": "command is empty"}
        return report
    if not 1 <= timeout <= MAX_TIMEOUT:
        report["error"] = {
            "type": "ValueError",
            "message": f"timeout must be between 1 and {MAX_TIMEOUT} seconds",
        }
        return report
    if not 256 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        report["error"] = {
            "type": "ValueError",
            "message": (
                f"max output must be between 256 and {MAX_OUTPUT_BYTES} bytes"),
        }
        return report

    started = time.monotonic()
    try:
        if runner is None or runner is subprocess.run:
            proc, duration, stdout_bytes, stderr_bytes = _bounded_process(
                args, cwd, timeout, max_output_bytes, env=env)
        else:
            kwargs = {
                "cwd": str(cwd) if cwd else None,
                "capture_output": True,
                "text": True,
                "timeout": timeout,
            }
            if env is not None:
                kwargs["env"] = dict(env)
            proc = runner(args, **kwargs)
            duration = round(time.monotonic() - started, 3)
            stdout_bytes = len(output_text(proc.stdout).encode())
            stderr_bytes = len(output_text(proc.stderr).encode())
            if stdout_bytes + stderr_bytes > max_output_bytes:
                raise OutputLimitExceeded(
                    max_output_bytes, output_text(proc.stdout).encode(),
                    output_text(proc.stderr).encode())
        report.update({
            "returncode": proc.returncode,
            "duration_seconds": duration,
            "stdout": output_text(proc.stdout),
            "stderr": output_text(proc.stderr),
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "complete": True,
            "state": "complete",
        })
    except subprocess.TimeoutExpired as exc:
        stdout = output_text(exc.output)
        stderr = output_text(exc.stderr)
        report.update({
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": stdout,
            "stderr": stderr,
            "stdout_bytes": len(stdout.encode()),
            "stderr_bytes": len(stderr.encode()),
            "timed_out": True,
            "truncated": True,
            "state": "timed-out",
            "error": {
                "type": "TimeoutExpired",
                "message": f"command timed out after {exc.timeout} seconds",
            },
        })
    except OutputLimitExceeded as exc:
        stdout_bytes, stderr_bytes = _bounded_prefix(
            exc.stdout, exc.stderr, max_output_bytes)
        report.update({
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": stdout_bytes.decode(errors="replace"),
            "stderr": stderr_bytes.decode(errors="replace"),
            "stdout_bytes": len(stdout_bytes),
            "stderr_bytes": len(stderr_bytes),
            "truncated": True,
            "state": "truncated",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })
    except Exception as exc:
        report.update({
            "duration_seconds": round(time.monotonic() - started, 3),
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })
    return report


def read_tool_version(
        command: Sequence[str], *, timeout: int = 30,
        max_output_bytes: int = 1024,
        env: Mapping[str, str] | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> dict:
    evidence = run_evidence_command(
        command, timeout=timeout, max_output_bytes=max_output_bytes,
        env=env, runner=runner)
    version = evidence["stdout"].strip()
    if (not evidence["complete"] or evidence["returncode"] != 0
            or not version or len(version.encode()) > max_output_bytes):
        version = None
    return {
        "command": evidence["command"],
        "version": version,
        "complete": version is not None,
        "execution": evidence,
    }


def identify_input(path: Path) -> dict:
    resolved = path.expanduser().resolve()
    identity = {
        "path": str(resolved),
        "kind": ("file" if resolved.is_file()
                 else "directory" if resolved.is_dir() else "missing"),
        "sha256": None,
        "git_root": None,
        "git_revision": None,
        "git_status": None,
        "state": "missing" if not resolved.exists() else "path-only",
    }
    if resolved.is_file():
        digest = hashlib.sha256()
        with resolved.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        identity["sha256"] = digest.hexdigest()
        git_start = resolved.parent
    else:
        git_start = resolved
    if not resolved.exists():
        return identity
    try:
        root_proc = subprocess.run(
            ["git", "-C", str(git_start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10)
        if root_proc.returncode != 0:
            return identity
        root = Path(root_proc.stdout.strip()).resolve()
        relative = resolved.relative_to(root)
        pathspec = str(relative) if relative.parts else "."
        revision_proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10)
        status_proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1",
             "--untracked-files=normal", "--", pathspec],
            capture_output=True, text=True, timeout=10)
        if revision_proc.returncode != 0 or status_proc.returncode != 0:
            return identity
        status = status_proc.stdout.splitlines()
        identity.update({
            "git_root": str(root),
            "git_revision": revision_proc.stdout.strip(),
            "git_status": status,
            "state": "git-revision-dirty" if status else "git-revision-clean",
        })
    except (OSError, subprocess.TimeoutExpired):
        pass
    return identity
