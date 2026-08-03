#!/usr/bin/env python3
"""Collect one reproducible Gentoo overlay skills maintenance-cycle report."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_ROOT = Path(__file__).resolve().parent
ROOT = SKILL_ROOT.parents[2]
SOURCE_MANAGER = (
    ROOT / ".agents" / "skills" / "gentoo-overlay-development" / "scripts"
    / "source_manager.py")
LESSON_LOOKUP = (
    ROOT / ".agents" / "skills" / "gzh-version-bump" / "scripts"
    / "lesson_lookup.py")
VALIDATOR = ROOT / "scripts" / "validate_repository.py"
CANONICAL_SLUG = "gentoo-zh/skills"
MAX_COMMAND_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_GIT_OUTPUT_BYTES = 256 * 1024


class BoundedProcessError(RuntimeError):
    def __init__(self, message: str, *, stdout: bytes, stderr: bytes,
                 timed_out: bool):
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.truncated = True


def load_evidence_store():
    path = SCRIPT_ROOT / "evidence_store.py"
    spec = importlib.util.spec_from_file_location(
        "gzh_maintenance_evidence_store", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the maintenance evidence store")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EvidenceStore


def output_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(encoding="utf-8", errors="replace")
    return value


def stop_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    if proc.poll() is None:
        proc.wait()


def bounded_process(command: list[str], *, timeout: float,
                    maximum: int) -> tuple[subprocess.CompletedProcess, float,
                                           int, int]:
    started = time.monotonic()
    proc = subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
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
                raise BoundedProcessError(
                    f"timed out after {timeout} seconds",
                    stdout=bytes(streams[stdout_fd]),
                    stderr=bytes(streams[stderr_fd]), timed_out=True)
            for key, _events in selector.select(min(remaining, 0.25)):
                current_size = sum(len(value) for value in streams.values())
                chunk = os.read(key.fd, min(65536, maximum - current_size + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                available = maximum - current_size
                streams[key.fd].extend(chunk[:available])
                if len(chunk) > available:
                    raise BoundedProcessError(
                        f"output exceeded {maximum} bytes",
                        stdout=bytes(streams[stdout_fd]),
                        stderr=bytes(streams[stderr_fd]), timed_out=False)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BoundedProcessError(
                f"timed out after {timeout} seconds",
                stdout=bytes(streams[stdout_fd]),
                stderr=bytes(streams[stderr_fd]), timed_out=True)
        try:
            returncode = proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired as exc:
            raise BoundedProcessError(
                f"timed out after {timeout} seconds",
                stdout=bytes(streams[stdout_fd]),
                stderr=bytes(streams[stderr_fd]), timed_out=True) from exc
    except Exception:
        stop_process_group(proc)
        raise
    finally:
        selector.close()
        proc.stdout.close()
        proc.stderr.close()
    duration = round(time.monotonic() - started, 3)
    stdout = bytes(streams[stdout_fd])
    stderr = bytes(streams[stderr_fd])
    return (subprocess.CompletedProcess(
        command, returncode, stdout=output_text(stdout),
        stderr=output_text(stderr)), duration, len(stdout), len(stderr))


def run_command(name: str, command: list[str], timeout: float = 900,
                max_output_bytes: int = MAX_COMMAND_OUTPUT_BYTES) -> dict:
    started = time.monotonic()
    try:
        proc, duration, stdout_bytes, stderr_bytes = bounded_process(
            command, timeout=timeout, maximum=max_output_bytes)
        return {
            "name": name,
            "command": command,
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "duration_seconds": duration,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "stdout_bytes": stdout_bytes,
            "stderr_bytes": stderr_bytes,
            "timed_out": False,
            "truncated": False,
        }
    except BoundedProcessError as exc:
        stderr = output_text(exc.stderr)
        if stderr:
            stderr += "\n"
        stderr += str(exc)
        return {
            "name": name,
            "command": command,
            "ok": False,
            "returncode": None,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout": output_text(exc.stdout),
            "stderr": stderr,
            "stdout_bytes": len(exc.stdout),
            "stderr_bytes": len(exc.stderr),
            "timed_out": exc.timed_out,
            "truncated": True,
        }


def github_slug(url: str) -> str | None:
    value = url.strip()
    if re.match(r"^[^/@:]+@github\.com:", value, flags=re.IGNORECASE):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        path = parsed.path
    return path.strip("/").removesuffix(".git").lower() or None


def canonical_remote() -> tuple[str, str]:
    names = checked_output(["git", "remote"]).splitlines()
    matches = []
    for name in names:
        url = checked_output(["git", "remote", "get-url", name]).strip()
        if github_slug(url) == CANONICAL_SLUG:
            matches.append((name, url))
    if len(matches) != 1:
        detail = "none" if not matches else ", ".join(name for name, _ in matches)
        raise RuntimeError(
            f"expected one canonical {CANONICAL_SLUG} remote; found {detail}")
    return matches[0]


def git_output(*arguments: str) -> str:
    return checked_output(["git", *arguments]).strip()


def checked_output(command: list[str]) -> str:
    proc, _duration, _stdout_bytes, _stderr_bytes = bounded_process(
        command, timeout=30, maximum=MAX_GIT_OUTPUT_BYTES)
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, command, output=proc.stdout, stderr=proc.stderr)
    return proc.stdout


def repository_state(fetch: bool) -> dict:
    remote, url = canonical_remote()
    fetch_result = None
    if fetch:
        fetch_result = run_command(
            "fetch", ["git", "fetch", remote, "master"], timeout=180)
    branch_proc, _duration, _stdout_bytes, _stderr_bytes = bounded_process(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        timeout=30, maximum=MAX_GIT_OUTPUT_BYTES)
    branch = branch_proc.stdout.strip() if branch_proc.returncode == 0 else None
    counts = git_output(
        "rev-list", "--left-right", "--count", f"HEAD...{remote}/master").split()
    if len(counts) != 2:
        raise RuntimeError("cannot determine canonical synchronization state")
    return {
        "head": git_output("rev-parse", "HEAD"),
        "branch": branch,
        "dirty": bool(git_output("status", "--porcelain")),
        "canonical_remote": remote,
        "canonical_url": url,
        "ahead": int(counts[0]),
        "behind": int(counts[1]),
        "fetch": fetch_result,
    }


def source_summary(step: dict) -> dict | None:
    try:
        records = json.loads(step["stdout"])
    except (json.JSONDecodeError, TypeError):
        return None
    states: dict[str, int] = {}
    for record in records:
        state = record.get("state", "unknown")
        states[state] = states.get(state, 0) + 1
    return {
        "total": len(records),
        "states": states,
        "attention": [record.get("id") for record in records
                      if record.get("state") != "current"],
    }


def source_records(step: dict) -> list[dict] | None:
    try:
        records = json.loads(step["stdout"])
    except (json.JSONDecodeError, TypeError):
        return None
    return records if isinstance(records, list) else None


def atomic_write(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def append_step(report: dict, step: dict) -> None:
    report["steps"].append(step)
    if step.get("truncated"):
        report["truncated"] = True


def render_markdown(report: dict) -> str:
    state = report.get("repository", {})
    result = "review required"
    if report["ok"]:
        result = "pass" if report.get("complete") else "scoped pass"
    lines = [
        "<!-- gentoo-zh-skills-maintenance:v1 -->",
        "# Skill maintenance cycle",
        "",
        f"- Result: {result}",
        f"- Head: `{state.get('head', 'unknown')}`",
        f"- Branch: `{state.get('branch') or 'detached'}`",
        f"- Canonical remote: `{state.get('canonical_remote', 'unknown')}`",
        f"- Ahead/behind: `{state.get('ahead', '?')}/{state.get('behind', '?')}`",
        f"- Dirty: `{'yes' if state.get('dirty') else 'no'}`",
        "",
        "| Gate | Result | Seconds |",
        "| --- | --- | ---: |",
    ]
    if report.get("repository_error"):
        lines.extend([
            "",
            "## Repository discovery",
            "",
            "```text",
            report["repository_error"],
            "```",
        ])
    skipped = report.get("skipped_gates", [])
    if skipped:
        lines.extend([
            "",
            "Skipped gates: " + ", ".join(f"`{name}`" for name in skipped) + ".",
        ])
    for step in report.get("steps", []):
        lines.append(
            f"| `{step['name']}` | {'pass' if step['ok'] else 'fail'} | "
            f"{step['duration_seconds']} |")
    summary = report.get("sources")
    if summary:
        states = ", ".join(
            f"{name}={count}" for name, count in sorted(summary["states"].items()))
        lines.extend(["", f"Registered sources: {summary['total']} ({states})."])
        if summary["attention"]:
            lines.append(
                "Review source IDs: " + ", ".join(f"`{item}`"
                                                   for item in summary["attention"]) + ".")
    failures = [step for step in report.get("steps", []) if not step["ok"]]
    for step in failures:
        detail = (step.get("stderr") or step.get("stdout") or "no output")[-3000:]
        lines.extend([
            "",
            f"## {step['name']}",
            "",
            "```text",
            detail.rstrip(),
            "```",
        ])
    return "\n".join(lines) + "\n"


def collect(args: argparse.Namespace) -> dict:
    requested_gates = []
    skipped_gates = []
    if args.skip_network:
        skipped_gates.extend(["source-audit", "lesson-refresh"])
    else:
        requested_gates.extend(["source-audit", "lesson-refresh"])
    requested_gates.append("repository-validator")
    if args.skip_tests:
        skipped_gates.append("tests")
    else:
        requested_gates.append("tests")
    requested_gates.append("diff-check")
    report = {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).replace(
            microsecond=0).isoformat().replace("+00:00", "Z"),
        "repository": {},
        "steps": [],
        "requested_gates": requested_gates,
        "skipped_gates": skipped_gates,
        "complete": False,
        "truncated": False,
    }
    try:
        report["repository"] = repository_state(args.fetch)
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        report["repository_error"] = str(exc)
        if isinstance(exc, BoundedProcessError):
            report["truncated"] = True
            report["repository_timed_out"] = exc.timed_out
            report["repository_truncated"] = True
        report["sources"] = None
        report["ok"] = False
        return report

    state = report["repository"]
    if state.get("fetch") is not None:
        append_step(report, state["fetch"])
        if not state["fetch"]["ok"]:
            report["sources"] = None
            report["ok"] = False
            return report
    if args.require_synced_master:
        state_ok = (state["branch"] == "master" and not state["dirty"]
                    and state["ahead"] == 0 and state["behind"] == 0)
        append_step(report, {
            "name": "repository-state",
            "command": [],
            "ok": state_ok,
            "returncode": 0 if state_ok else 1,
            "duration_seconds": 0.0,
            "stdout": "",
            "stderr": ("" if state_ok else
                       "expected a clean, synchronized master checkout"),
            "timed_out": False,
            "truncated": False,
        })
        if not state_ok:
            report["sources"] = None
            report["ok"] = False
            return report

    if not args.skip_network:
        source = run_command(
            "source-audit",
            [sys.executable, str(SOURCE_MANAGER), "audit", "--all-scopes",
             "--fail-on-drift"],
            timeout=300)
        append_step(report, source)
        report["sources"] = source_summary(source)
        report["source_records"] = source_records(source)
        if not source["ok"]:
            report["ok"] = False
            return report
        lesson = run_command(
            "lesson-refresh",
            [sys.executable, str(LESSON_LOOKUP), "--refresh", "--stats"],
            timeout=300)
        append_step(report, lesson)
        if not lesson["ok"]:
            report["ok"] = False
            return report
    validator = run_command(
        "repository-validator", [sys.executable, str(VALIDATOR)])
    append_step(report, validator)
    if not validator["ok"]:
        report["ok"] = False
        return report
    if not args.skip_tests:
        tests = run_command(
            "tests", [sys.executable, "-m", "pytest", "-q", "gzh/tests", "tests"])
        append_step(report, tests)
        if not tests["ok"]:
            report["ok"] = False
            return report
    diff = run_command("diff-check", ["git", "diff", "--check"])
    append_step(report, diff)
    source = next(
        (step for step in report["steps"] if step["name"] == "source-audit"),
        None)
    report["sources"] = source_summary(source) if source else None
    report["ok"] = all(step["ok"] for step in report["steps"])
    report["complete"] = report["ok"] and not report["skipped_gates"]
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true",
                        help="fetch canonical master before recording repository state")
    parser.add_argument(
        "--require-synced-master", action="store_true",
        help="fail unless HEAD is clean master with zero canonical ahead/behind counts")
    parser.add_argument("--skip-network", action="store_true",
                        help="skip source and lesson network checks")
    parser.add_argument("--skip-tests", action="store_true",
                        help="skip pytest for focused script validation")
    parser.add_argument("--output", type=Path, help="write the JSON report atomically")
    parser.add_argument("--markdown-output", type=Path,
                        help="write a concise Markdown report atomically")
    parser.add_argument("--evidence-db", type=Path,
                        help="ingest this run into a SQLite evidence database")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = collect(args)
    if args.evidence_db:
        evidence_step = {
            "name": "evidence-store",
            "command": [],
            "ok": True,
            "returncode": 0,
            "duration_seconds": 0.0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        append_step(report, evidence_step)
        try:
            evidence_store = load_evidence_store()
            with evidence_store(args.evidence_db) as store:
                store.ingest(report, "maintenance-cycle")
        except Exception as exc:
            report["evidence_error"] = f"{type(exc).__name__}: {exc}"
            evidence_step["ok"] = False
            evidence_step["returncode"] = 1
            evidence_step["stderr"] = report["evidence_error"]
            report["ok"] = False
            report["complete"] = False
    payload = json.dumps(report, indent=2) + "\n"
    if args.output:
        atomic_write(args.output, payload)
    if args.markdown_output:
        atomic_write(args.markdown_output, render_markdown(report))
    if args.output:
        state = "complete" if report["complete"] and not report["truncated"] else "incomplete"
        print(f"Wrote {state} maintenance report to {args.output}.")
    else:
        print(payload, end="")
    return int(not report["ok"])


if __name__ == "__main__":
    raise SystemExit(main())
