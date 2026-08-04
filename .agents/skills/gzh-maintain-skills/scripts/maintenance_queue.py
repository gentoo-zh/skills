#!/usr/bin/env python3
"""Run a durable, allowlisted Gentoo skill maintenance task queue."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import importlib.util
import json
import os
import re
import selectors
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[3]
SOURCE_MANAGER = (
    ROOT / ".agents" / "skills" / "gentoo-overlay-development" / "scripts"
    / "source_manager.py")
LESSON_LOOKUP = (
    ROOT / ".agents" / "skills" / "gzh-version-bump" / "scripts"
    / "lesson_lookup.py")
QA_COLLECTOR = SCRIPT_DIR / "qa_style_collector.py"
DEPENDENCY_ANALYZER = (
    ROOT / ".agents" / "skills" / "gentoo-overlay-development" / "scripts"
    / "dependency_analyzer.py")
VALIDATOR = ROOT / "scripts" / "validate_repository.py"
RELEASE_CHECK = ROOT / "scripts" / "release_check.py"
SCHEMA_VERSION = 1
TASK_TIMEOUT_SECONDS = 1800
LEASE_GRACE_SECONDS = 300
MAX_TASK_OUTPUT_BYTES = 256 * 1024
PRODUCER_TASKS = {"qa-style-collect", "dependency-analyze"}
ALLOWED_TASKS = {
    "source-audit",
    "lesson-refresh",
    "qa-style-collect",
    "dependency-analyze",
    "repository-validator",
    "release-check",
    "tests",
    "diff-check",
}


class ProcessResult(NamedTuple):
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    truncated: bool = False


def terminate_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        proc.kill()


def run_bounded(command: list[str], *, cwd: Path, timeout: int,
                max_output_bytes: int = MAX_TASK_OUTPUT_BYTES) -> ProcessResult:
    if timeout <= 0 or max_output_bytes <= 0:
        raise ValueError("positive process timeout and output limit are required")
    proc = subprocess.Popen(
        command, cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        start_new_session=True)
    assert proc.stdout is not None and proc.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
    selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    started = time.monotonic()
    timed_out = False
    truncated = False
    try:
        while selector.get_map():
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
                terminate_process_group(proc)
                break
            for key, _mask in selector.select(min(remaining, 1.0)):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                available = max_output_bytes - total
                if len(chunk) > available:
                    buffers[key.data].extend(chunk[:available])
                    total += available
                    truncated = True
                    terminate_process_group(proc)
                    break
                buffers[key.data].extend(chunk)
                total += len(chunk)
            if truncated:
                break
    finally:
        for key in list(selector.get_map().values()):
            selector.unregister(key.fileobj)
            key.fileobj.close()
        selector.close()
    try:
        returncode = proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        terminate_process_group(proc)
        returncode = proc.wait(timeout=5)
    return ProcessResult(
        returncode=returncode,
        stdout=buffers["stdout"].decode("utf-8", errors="replace"),
        stderr=buffers["stderr"].decode("utf-8", errors="replace"),
        timed_out=timed_out,
        truncated=truncated,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def payload_record(value: dict) -> tuple[str, str, int]:
    text = canonical_json(value)
    encoded = text.encode("utf-8")
    return text, hashlib.sha256(encoded).hexdigest(), len(encoded)


def normalized_failure_text(value: str) -> str:
    text = value[-8192:]
    text = re.sub(r"/tmp/[^\s:'\"]+", "/tmp/<path>", text)
    text = re.sub(
        r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\b",
        "<timestamp>", text)
    text = re.sub(r"pytest-\d+", "pytest-<run>", text)
    text = re.sub(r"\b\d+(?:\.\d+)?s\b", "<duration>", text)
    return text


def failure_fingerprint(kind: str, result: dict) -> str:
    detail = result.get("stderr") or result.get("stdout") or ""
    return hashlib.sha256(canonical_json({
        "kind": kind,
        "failure_code": result.get("failure_code", "process-exit"),
        "returncode": result.get("returncode"),
        "detail": normalized_failure_text(detail),
    }).encode()).hexdigest()


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


def persist_result(path: Path, result: dict) -> None:
    persisted = dict(result)
    persisted.pop("result_path", None)
    persisted.pop("result_sha256", None)
    content = json.dumps(persisted, ensure_ascii=False, indent=2) + "\n"
    atomic_write(path, content)
    result["result_path"] = str(path.resolve())
    result["result_sha256"] = hashlib.sha256(content.encode()).hexdigest()


def validate_payload(kind: str, payload: dict) -> None:
    if kind not in ALLOWED_TASKS:
        raise ValueError(f"task kind is not allowlisted: {kind}")
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA_VERSION:
        raise ValueError("task payload must use schema 1")
    if payload.get("task") != kind:
        raise ValueError("task payload kind does not match the queue task")
    if not isinstance(payload.get("parameters", {}), dict):
        raise ValueError("task parameters must be an object")


def exact_parameters(payload: dict, allowed: set[str]) -> dict:
    parameters = payload.get("parameters", {})
    unexpected = set(parameters) - allowed
    if unexpected:
        raise ValueError(
            "unsupported task parameters: " + ", ".join(sorted(unexpected)))
    return parameters


def task_command(kind: str, payload: dict, result_path: Path) -> list[str]:
    validate_payload(kind, payload)
    if kind == "source-audit":
        exact_parameters(payload, set())
        return [sys.executable, str(SOURCE_MANAGER), "audit", "--all-scopes",
                "--fail-on-drift"]
    if kind == "lesson-refresh":
        exact_parameters(payload, set())
        return [sys.executable, str(LESSON_LOOKUP), "--refresh", "--stats"]
    if kind == "repository-validator":
        exact_parameters(payload, set())
        return [sys.executable, str(VALIDATOR)]
    if kind == "release-check":
        exact_parameters(payload, set())
        return [sys.executable, str(RELEASE_CHECK),
                "--mode", "source-only"]
    if kind == "tests":
        parameters = exact_parameters(payload, {"targets"})
        targets = parameters.get("targets", ["gzh/tests", "tests"])
        if (not isinstance(targets, list) or not targets
                or any(target not in {"gzh/tests", "tests"} for target in targets)):
            raise ValueError("test targets must be gzh/tests or tests")
        return [sys.executable, "-m", "pytest", "-q", *targets]
    if kind == "diff-check":
        exact_parameters(payload, set())
        return ["git", "diff", "--check"]
    if kind == "dependency-analyze":
        parameters = exact_parameters(
            payload, {"input", "input_sha256", "input_bytes"})
        input_path = parameters.get("input")
        if not isinstance(input_path, str) or not input_path:
            raise ValueError("dependency analysis requires an input path")
        if input_path == "-":
            raise ValueError("queued dependency analysis requires a file input")
        resolved_input = Path(input_path).expanduser()
        if not resolved_input.is_absolute():
            raise ValueError(
                "queued dependency analysis requires an absolute input path")
        expected_hash = parameters.get("input_sha256")
        expected_bytes = parameters.get("input_bytes")
        if (not isinstance(expected_hash, str)
                or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                or not isinstance(expected_bytes, int) or expected_bytes < 0):
            raise ValueError(
                "dependency analysis requires an input hash and byte count")
        content = resolved_input.read_bytes()
        if (len(content) != expected_bytes
                or hashlib.sha256(content).hexdigest() != expected_hash):
            raise ValueError("dependency analysis input changed after enqueue")
        return [sys.executable, str(DEPENDENCY_ANALYZER),
                "--input", input_path, "--output", str(result_path)]

    parameters = exact_parameters(payload, {
        "overlay_path", "overlay_url", "adapter_id", "canonical_repository",
        "canonical_url", "ref", "after_revision", "limit", "initial_depth", "workers",
        "audit_sources",
    })
    overlay_path = parameters.get("overlay_path")
    overlay_url = parameters.get("overlay_url")
    if bool(overlay_path) == bool(overlay_url):
        raise ValueError(
            "QA/style collection requires exactly one overlay path or URL")
    canonical_url = parameters.get("canonical_url")
    if overlay_path:
        if (not isinstance(canonical_url, str)
                or not canonical_url.startswith("https://")):
            raise ValueError(
                "local QA/style collection requires a canonical HTTPS URL")
    elif canonical_url is not None:
        raise ValueError(
            "remote QA/style collection derives its canonical URL from overlay_url")
    adapter_id = parameters.get("adapter_id")
    canonical_repository = parameters.get("canonical_repository")
    ref = parameters.get("ref")
    after_revision = parameters.get("after_revision")
    if not all(isinstance(value, str) and value for value in (
            adapter_id, canonical_repository, ref, after_revision)):
        raise ValueError(
            "QA/style collection requires adapter, repository, ref, and cursor")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", after_revision):
        raise ValueError("QA/style collection cursor must be an immutable commit ID")
    if not parameters.get("audit_sources") is True:
        raise ValueError("QA/style collection requires official source auditing")
    command = [sys.executable, str(QA_COLLECTOR)]
    if overlay_path:
        command.extend(["--overlay-path", str(overlay_path)])
        command.extend(["--canonical-url", canonical_url])
    else:
        command.extend(["--overlay-url", str(overlay_url)])
    command.extend([
        "--adapter-id", adapter_id,
        "--canonical-repository", canonical_repository,
        "--ref", ref,
        "--after-revision", after_revision,
        "--audit-sources",
    ])
    for key, option, minimum, maximum in (
            ("limit", "--limit", 1, 1000),
            ("initial_depth", "--initial-depth", 1, 1000),
            ("workers", "--workers", 1, 32)):
        value = parameters.get(key)
        if value is None:
            continue
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(
                f"{key} must be between {minimum} and {maximum}")
        command.extend([option, str(value)])
    command.extend(["--output", str(result_path)])
    return command


def load_evidence_store():
    path = SCRIPT_DIR / "evidence_store.py"
    spec = importlib.util.spec_from_file_location(
        "gzh_queue_evidence_store", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the maintenance evidence store")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.EvidenceStore


def validate_producer_artifact(kind: str, path: Path, payload: dict) -> dict:
    if kind not in PRODUCER_TASKS:
        raise ValueError(f"task is not a structured producer: {kind}")
    if not path.is_file():
        raise ValueError("structured producer did not create its artifact")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"structured producer artifact is invalid: {exc}") from exc
    schema = report.get("schema_version", report.get("schema"))
    if schema != SCHEMA_VERSION:
        raise ValueError("structured producer artifact has an unsupported schema")
    if (report.get("ok") is not True or report.get("complete") is not True
            or report.get("truncated") is not False):
        raise ValueError("structured producer artifact is failed or incomplete")
    if kind == "dependency-analyze":
        parameters = exact_parameters(
            payload, {"input", "input_sha256", "input_bytes"})
        provenance = report.get("input_provenance")
        if (report.get("tool") != "gentoo-overlay-dependency-analyzer"
                or not isinstance(report.get("engine"), dict)
                or not isinstance(report.get("eapi"), str)
                or not isinstance(report.get("fields"), dict)
                or not isinstance(report.get("atoms"), list)
                or not isinstance(provenance, dict)
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(provenance.get("sha256", "")))
                or not isinstance(provenance.get("bytes"), int)
                or provenance["bytes"] < 0
                or provenance.get("source") != parameters.get("input")
                or provenance.get("sha256") != parameters.get("input_sha256")
                or provenance.get("bytes") != parameters.get("input_bytes")):
            raise ValueError(
                "dependency producer artifact lacks its required identity or fields")
        return report

    scope = report.get("scope")
    history = report.get("history")
    sources = report.get("source_records")
    candidates = report.get("candidates")
    parameters = exact_parameters(payload, {
        "overlay_path", "overlay_url", "adapter_id", "canonical_repository",
        "canonical_url", "ref", "after_revision", "limit", "initial_depth", "workers",
        "audit_sources",
    })
    if (report.get("tool") != "gentoo-overlay-qa-style-collector"
            or report.get("history_complete") is not True
            or report.get("primary_validation_complete") is not True
            or report.get("output_complete") is not True
            or not isinstance(scope, dict)
            or not isinstance(history, dict)
            or not isinstance(sources, list)
            or not isinstance(candidates, list)
            or not all(isinstance(scope.get(key), str) and scope[key]
                       for key in ("adapter_id", "canonical_repository",
                                   "resolved_ref"))
            or scope.get("adapter_id") != parameters.get("adapter_id")
            or scope.get("canonical_repository")
            != parameters.get("canonical_repository")
            or scope.get("requested_ref") != parameters.get("ref")
            or history.get("cursor_state") != "verified"
            or history.get("complete") is not True
            or history.get("truncated") is not False
            or not re.fullmatch(
                r"[0-9a-fA-F]{40,64}",
                str(history.get("after_revision", "")))
            or history.get("after_revision")
            != parameters.get("after_revision")):
        raise ValueError(
            "QA/style producer artifact lacks its required identity or cursor")
    cursors = [source for source in sources
               if isinstance(source, dict)
               and source.get("id") == "scope-cursor"]
    if len(cursors) != 1:
        raise ValueError("QA/style producer artifact has no unique scope cursor")
    cursor = cursors[0]
    if (cursor.get("revision") != scope["resolved_ref"]
            or cursor.get("adapter_id") != scope["adapter_id"]
            or cursor.get("canonical_repository")
            != scope["canonical_repository"]
            or cursor.get("complete") is not True
            or cursor.get("truncated") is not False):
        raise ValueError("QA/style producer scope cursor does not match its scope")
    expected_location = parameters.get("overlay_url")
    if expected_location is None:
        expected_location = str(
            Path(parameters["overlay_path"]).expanduser().resolve())
    if scope.get("location") != expected_location:
        raise ValueError("QA/style producer input location does not match its task")
    expected_source_url = (scope.get("canonical_origin")
                           or scope.get("configured_origin")
                           or scope["location"])
    if parameters.get("canonical_url") is not None:
        if scope.get("canonical_origin") != parameters["canonical_url"]:
            raise ValueError(
                "QA/style producer canonical URL does not match its task")
    if (cursor.get("source_id") != "scope-cursor"
            or cursor.get("authority") != "repository-cursor"
            or cursor.get("url") != expected_source_url
            or cursor.get("state") != "observed"
            or cursor.get("validated") is not False
            or cursor.get("role") != "cursor"
            or cursor.get("topics") != []
            or cursor.get("repo_name") != scope.get("repo_name")):
        raise ValueError("QA/style producer scope cursor contract is invalid")
    source_index = {
        (source.get("id"), source.get("url"), source.get("revision")): source
        for source in sources if isinstance(source, dict)
    }
    if any(
            not isinstance(candidate, dict)
            or candidate.get("authority") != "candidate-history"
            or candidate.get("policy_status") != "not-established"
            or candidate.get("source_id")
            != f"candidate-history:{candidate.get('source_revision')}"
            or candidate.get("source_url") != expected_source_url
            or (candidate.get("source_id"), candidate.get("source_url"),
                candidate.get("source_revision")) not in source_index
            or candidate.get("scope") != scope["canonical_repository"]
            or candidate.get("adapter_id") != scope["adapter_id"]
            for candidate in candidates):
        raise ValueError("QA/style producer candidate provenance is incomplete")
    for candidate in candidates:
        source = source_index[(
            candidate["source_id"], candidate["source_url"],
            candidate["source_revision"])]
        if (source.get("source_id") != candidate["source_id"]
                or source.get("authority") != "candidate-history"
                or source.get("state") != "observed"
                or source.get("validated") is not False
                or source.get("role") != "candidate"
                or source.get("adapter_id") != scope["adapter_id"]
                or source.get("canonical_repository")
                != scope["canonical_repository"]
                or source.get("repo_name") != scope.get("repo_name")
                or candidate.get("topic") not in source.get("topics", [])):
            raise ValueError(
                "QA/style producer candidate source contract is invalid")
    return report


class MaintenanceQueue:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.initialize()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "MaintenanceQueue":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def initialize(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS maintenance_queue_schema (
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS maintenance_tasks (
                task_key TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                input_bytes INTEGER NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'running', 'succeeded', 'blocked')),
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL,
                last_failure_fingerprint TEXT,
                same_failure_count INTEGER NOT NULL DEFAULT 0,
                lease_owner TEXT,
                lease_token TEXT,
                lease_until INTEGER,
                result_path TEXT,
                result_sha256 TEXT,
                artifact_path TEXT,
                artifact_sha256 TEXT,
                stdout TEXT,
                stderr TEXT,
                returncode INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(plan_id, position)
            );
        """)
        rows = self.connection.execute(
            "SELECT version FROM maintenance_queue_schema").fetchall()
        if not rows:
            self.connection.execute(
                "INSERT INTO maintenance_queue_schema(version) VALUES (?)",
                (SCHEMA_VERSION,))
        elif len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
            raise ValueError("unsupported maintenance queue schema")
        self.connection.commit()

    def enqueue(self, *, task_key: str, plan_id: str, position: int,
                kind: str, payload: dict, max_attempts: int = 3) -> dict:
        if not task_key or not plan_id or position < 0:
            raise ValueError("task key, plan id, and non-negative position are required")
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        validate_payload(kind, payload)
        task_command(kind, payload, Path("/queue-validation-output"))
        payload_json, payload_hash, input_bytes = payload_record(payload)
        now = utc_now()
        with self.connection:
            existing = self.connection.execute(
                "SELECT * FROM maintenance_tasks WHERE task_key = ?",
                (task_key,)).fetchone()
            if existing:
                if (existing["payload_sha256"] != payload_hash
                        or existing["input_bytes"] != input_bytes
                        or existing["kind"] != kind
                        or existing["plan_id"] != plan_id
                        or existing["position"] != position):
                    raise ValueError(
                        "task key already exists with different complete input")
                return dict(existing)
            self.connection.execute("""
                INSERT INTO maintenance_tasks(
                    task_key, plan_id, position, kind, payload_json,
                    payload_sha256, input_bytes, status, max_attempts,
                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """, (task_key, plan_id, position, kind, payload_json,
                  payload_hash, input_bytes, max_attempts, now, now))
            row = self.connection.execute(
                "SELECT * FROM maintenance_tasks WHERE task_key = ?",
                (task_key,)).fetchone()
            self._verify_input(row)
            return dict(row)

    @staticmethod
    def _verify_input(row: sqlite3.Row) -> dict:
        payload = json.loads(row["payload_json"])
        payload_json, payload_hash, input_bytes = payload_record(payload)
        if (payload_json != row["payload_json"]
                or payload_hash != row["payload_sha256"]
                or input_bytes != row["input_bytes"]):
            raise ValueError(f"stored task input is incomplete: {row['task_key']}")
        validate_payload(row["kind"], payload)
        return payload

    def claim(self, plan_id: str, worker: str,
              lease_seconds: int = (
                  TASK_TIMEOUT_SECONDS + LEASE_GRACE_SECONDS)) -> dict | None:
        if not worker or not 30 <= lease_seconds <= 3600:
            raise ValueError("worker and a 30-3600 second lease are required")
        now_epoch = int(time.time())
        now = utc_now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            expired = self.connection.execute("""
                SELECT * FROM maintenance_tasks
                WHERE plan_id = ? AND status = 'running' AND lease_until < ?
                ORDER BY position
            """, (plan_id, now_epoch)).fetchall()
            lease_fingerprint = hashlib.sha256(
                b"worker lease expired before completion").hexdigest()
            for expired_row in expired:
                same_count = (
                    expired_row["same_failure_count"] + 1
                    if expired_row["last_failure_fingerprint"] == lease_fingerprint
                    else 1)
                expired_status = (
                    "blocked" if same_count >= 2
                    or expired_row["attempts"] >= expired_row["max_attempts"]
                    else "pending")
                self.connection.execute("""
                    UPDATE maintenance_tasks
                    SET status = ?, last_failure_fingerprint = ?,
                        same_failure_count = ?, lease_owner = NULL,
                        lease_token = NULL, lease_until = NULL,
                        stderr = ?, returncode = NULL, updated_at = ?
                    WHERE task_key = ? AND status = 'running'
                        AND lease_token = ?
                """, (expired_status, lease_fingerprint, same_count,
                      "worker lease expired before completion", now,
                      expired_row["task_key"], expired_row["lease_token"]))
            row = self.connection.execute("""
                SELECT * FROM maintenance_tasks
                WHERE plan_id = ? AND status != 'succeeded'
                ORDER BY position LIMIT 1
            """, (plan_id,)).fetchone()
            if row is None or row["status"] != "pending":
                self.connection.commit()
                return None
            self._verify_input(row)
            lease_token = uuid.uuid4().hex
            cursor = self.connection.execute("""
                UPDATE maintenance_tasks
                SET status = 'running', attempts = attempts + 1,
                    lease_owner = ?, lease_token = ?, lease_until = ?, updated_at = ?
                WHERE task_key = ? AND status = 'pending'
            """, (worker, lease_token, now_epoch + lease_seconds, now,
                  row["task_key"]))
            if cursor.rowcount != 1:
                raise RuntimeError("task claim compare-and-swap failed")
            claimed = self.connection.execute(
                "SELECT * FROM maintenance_tasks WHERE task_key = ?",
                (row["task_key"],)).fetchone()
            self.connection.commit()
            return dict(claimed)
        except Exception:
            self.connection.rollback()
            raise

    def finish(self, task_key: str, worker: str, lease_token: str,
               result: dict, *, manage_transaction: bool = True) -> dict:
        row = self.connection.execute(
            "SELECT * FROM maintenance_tasks WHERE task_key = ?",
            (task_key,)).fetchone()
        if (row is None or row["status"] != "running"
                or row["lease_owner"] != worker
                or row["lease_token"] != lease_token):
            raise ValueError("task is not leased by this worker")
        failure_hash = None
        same_failure_count = 0
        status = "succeeded"
        if not result["ok"]:
            failure_fingerprint_value = failure_fingerprint(row["kind"], result)
            same_failure_count = (
                row["same_failure_count"] + 1
                if row["last_failure_fingerprint"]
                == failure_fingerprint_value else 1)
            status = (
                "blocked" if same_failure_count >= 2
                or row["attempts"] >= row["max_attempts"] else "pending")
            failure_hash = failure_fingerprint_value
        transaction = self.connection if manage_transaction else nullcontext()
        with transaction:
            cursor = self.connection.execute("""
                UPDATE maintenance_tasks
                SET status = ?, last_failure_fingerprint = ?,
                    same_failure_count = ?, lease_owner = NULL,
                    lease_token = NULL, lease_until = NULL,
                    result_path = ?, result_sha256 = ?, stdout = ?, stderr = ?,
                    artifact_path = ?, artifact_sha256 = ?,
                    returncode = ?, updated_at = ?
                WHERE task_key = ? AND status = 'running' AND lease_owner = ?
                    AND lease_token = ?
            """, (status, failure_hash, same_failure_count,
                  result.get("result_path"), result.get("result_sha256"),
                  result.get("stdout", ""), result.get("stderr", ""),
                  result.get("artifact_path"), result.get("artifact_sha256"),
                  result.get("returncode"), utc_now(), task_key, worker,
                  lease_token))
            if cursor.rowcount != 1:
                raise RuntimeError("task completion compare-and-swap failed")
        return dict(self.connection.execute(
            "SELECT * FROM maintenance_tasks WHERE task_key = ?",
            (task_key,)).fetchone())

    def status(self, plan_id: str) -> dict:
        rows = [dict(row) for row in self.connection.execute("""
            SELECT * FROM maintenance_tasks WHERE plan_id = ? ORDER BY position
        """, (plan_id,))]
        states: dict[str, int] = {}
        for row in rows:
            states[row["status"]] = states.get(row["status"], 0) + 1
        return {
            "schema": SCHEMA_VERSION,
            "plan_id": plan_id,
            "tasks": rows,
            "states": states,
            "complete": bool(rows) and all(
                row["status"] == "succeeded" for row in rows),
            "blocked": any(row["status"] == "blocked" for row in rows),
        }

    def find_open_plan(self, head_sha: str, adapter_id: str,
                       canonical_repository: str) -> dict | None:
        if (not re.fullmatch(r"[0-9a-fA-F]{40,64}", head_sha)
                or not adapter_id or not canonical_repository):
            raise ValueError(
                "head SHA, adapter ID, and canonical repository are required")
        plan_ids = [row["plan_id"] for row in self.connection.execute(
            """
            SELECT plan_id
            FROM maintenance_tasks
            GROUP BY plan_id
            HAVING SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END)
                < COUNT(*)
            ORDER BY MAX(updated_at) DESC, plan_id DESC
            """)]
        matches = []
        for plan_id in plan_ids:
            tasks = self.status(plan_id)["tasks"]
            contexts = []
            for task in tasks:
                stored = self._verify_input(task)
                context = stored.get("input")
                contexts.append(context)
            if (not contexts or any(context != contexts[0] for context in contexts)
                    or not isinstance(contexts[0], dict)
                    or (contexts[0].get("iteration") or {}).get("head_sha")
                    != head_sha):
                continue
            qa_tasks = [task for task in tasks
                        if task["kind"] == "qa-style-collect"]
            if len(qa_tasks) != 1:
                continue
            parameters = json.loads(qa_tasks[0]["payload_json"])["parameters"]
            if (parameters.get("adapter_id") != adapter_id
                    or parameters.get("canonical_repository")
                    != canonical_repository):
                continue
            matches.append({
                "schema": SCHEMA_VERSION,
                "plan_id": plan_id,
                "head_sha": head_sha,
                "adapter_id": adapter_id,
                "canonical_repository": canonical_repository,
                "after_revision": parameters["after_revision"],
                "blocked": any(task["status"] == "blocked" for task in tasks),
            })
        if len(matches) > 1:
            raise ValueError(
                "multiple incomplete maintenance plans match this repository state")
        return matches[0] if matches else None

    def compact(self, keep_plans: int = 16) -> dict:
        if not isinstance(keep_plans, int) or not 1 <= keep_plans <= 100:
            raise ValueError("queue retention must keep between 1 and 100 plans")
        plans = self.connection.execute("""
            SELECT plan_id, MAX(updated_at) AS last_updated,
                COUNT(*) AS tasks,
                SUM(CASE WHEN status != 'succeeded' THEN 1 ELSE 0 END)
                    AS incomplete_tasks
            FROM maintenance_tasks
            GROUP BY plan_id
            ORDER BY last_updated DESC, plan_id DESC
        """).fetchall()
        incomplete = [plan for plan in plans if plan["incomplete_tasks"]]
        if len(incomplete) > keep_plans:
            raise ValueError(
                "incomplete maintenance plan backlog exceeds queue retention")
        completed = [plan for plan in plans if not plan["incomplete_tasks"]]
        completed_slots = keep_plans - len(incomplete)
        removed = completed[completed_slots:]
        with self.connection:
            for plan in removed:
                self.connection.execute(
                    "DELETE FROM maintenance_tasks WHERE plan_id = ?",
                    (plan["plan_id"],))
        if removed:
            self.connection.execute("VACUUM")
        return {
            "schema": SCHEMA_VERSION,
            "plans_before": len(plans),
            "plans_kept": len(plans) - len(removed),
            "plans_removed": len(removed),
            "tasks_removed": sum(plan["tasks"] for plan in removed),
        }


def execute_one(queue: MaintenanceQueue, plan_id: str, worker: str,
                output_dir: Path,
                evidence_db: Path | None = None) -> dict | None:
    task = queue.claim(plan_id, worker)
    if task is None:
        return None
    payload = json.loads(task["payload_json"])
    plan_directory = output_dir / hashlib.sha256(plan_id.encode()).hexdigest()[:16]
    identity = hashlib.sha256(task["task_key"].encode()).hexdigest()[:16]
    basename = (
        f"{identity}-attempt-{task['attempts']}-{task['lease_token']}")
    artifact_path = plan_directory / f"{basename}.artifact.json"
    result_path = plan_directory / f"{basename}.result.json"
    producer_report = None
    try:
        plan_directory.mkdir(parents=True, exist_ok=True)
        command = task_command(task["kind"], payload, artifact_path)
        proc = run_bounded(
            command, cwd=ROOT, timeout=TASK_TIMEOUT_SECONDS)
        result = {
            "schema": SCHEMA_VERSION,
            "task_key": task["task_key"],
            "kind": task["kind"],
            "payload_sha256": task["payload_sha256"],
            "input_bytes": task["input_bytes"],
            "command": command,
            "ok": (proc.returncode == 0
                   and not proc.timed_out and not proc.truncated),
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "completed_at": utc_now(),
        }
        if proc.timed_out:
            result["stderr"] += (
                f"\ntimed out after {TASK_TIMEOUT_SECONDS} seconds")
        if proc.truncated:
            result["stderr"] += (
                f"\ncombined process output exceeded "
                f"{MAX_TASK_OUTPUT_BYTES} bytes")
        if result["ok"] and task["kind"] in PRODUCER_TASKS:
            producer_report = validate_producer_artifact(
                task["kind"], artifact_path, payload)
    except subprocess.TimeoutExpired as exc:
        result = {
            "schema": SCHEMA_VERSION,
            "task_key": task["task_key"],
            "kind": task["kind"],
            "payload_sha256": task["payload_sha256"],
            "input_bytes": task["input_bytes"],
            "command": [],
            "ok": False,
            "returncode": None,
            "stdout": (exc.stdout.decode(errors="replace")
                       if isinstance(exc.stdout, bytes) else exc.stdout or ""),
            "stderr": (exc.stderr.decode(errors="replace")
                       if isinstance(exc.stderr, bytes) else exc.stderr or "")
                      + f"\ntimed out after {exc.timeout} seconds",
            "completed_at": utc_now(),
        }
    except Exception as exc:
        result = {
            "schema": SCHEMA_VERSION,
            "task_key": task["task_key"],
            "kind": task["kind"],
            "payload_sha256": task["payload_sha256"],
            "input_bytes": task["input_bytes"],
            "command": [],
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "completed_at": utc_now(),
        }
    try:
        if artifact_path.is_file():
            artifact = artifact_path.read_bytes()
            result["artifact_path"] = str(artifact_path.resolve())
            result["artifact_sha256"] = hashlib.sha256(artifact).hexdigest()
    except OSError as exc:
        result["ok"] = False
        result["stderr"] = (
            result.get("stderr", "") +
            f"\nartifact read failed: {type(exc).__name__}: {exc}").lstrip()
    if result["ok"] and producer_report is not None and evidence_db is None:
        result["ok"] = False
        result["stderr"] = (
            result.get("stderr", "")
            + "\nproducer tasks require the queue evidence database"
        ).lstrip()
    try:
        persist_result(result_path, result)
    except OSError as exc:
        result["ok"] = False
        result["stderr"] = (
            result.get("stderr", "") +
            f"\nresult persistence failed: {type(exc).__name__}: {exc}").lstrip()
    if result["ok"] and producer_report is not None:
        try:
            if evidence_db.expanduser().resolve() != queue.path:
                raise ValueError(
                    "producer evidence database must be the queue database")
            evidence_store = load_evidence_store()
            with evidence_store(evidence_db):
                pass
            queue.connection.execute("BEGIN IMMEDIATE")
            try:
                store = evidence_store.from_connection(queue.connection)
                result["evidence"] = store.ingest(
                    producer_report, task["kind"], manage_transaction=False)
                persist_result(result_path, result)
                finished = queue.finish(
                    task["task_key"], worker, task["lease_token"], result,
                    manage_transaction=False)
                queue.connection.commit()
                return finished
            except Exception:
                queue.connection.rollback()
                raise
        except Exception as exc:
            result["ok"] = False
            result["stderr"] = (
                result.get("stderr", "") +
                f"\nevidence transaction failed: {type(exc).__name__}: {exc}"
            ).lstrip()
            result.pop("result_path", None)
            result.pop("result_sha256", None)
            try:
                persist_result(result_path, result)
            except OSError as persist_exc:
                result.pop("result_path", None)
                result.pop("result_sha256", None)
                try:
                    result_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    result["stderr"] += (
                        f"\nstale result cleanup failed: "
                        f"{type(cleanup_exc).__name__}: {cleanup_exc}")
                result["stderr"] += (
                    f"\nresult persistence failed: "
                    f"{type(persist_exc).__name__}: {persist_exc}")
    return queue.finish(
        task["task_key"], worker, task["lease_token"], result)


def enqueue_default(queue: MaintenanceQueue, plan_id: str, context: Any,
                    overlay_url: str | None,
                    adapter_id: str | None = None,
                    canonical_repository: str | None = None,
                    overlay_ref: str | None = None,
                    after_revision: str | None = None,
                    limit: int = 500) -> None:
    overlay_fields = (
        adapter_id, canonical_repository, overlay_ref, after_revision)
    if overlay_url and not all(overlay_fields):
        raise ValueError(
            "overlay collection requires adapter, repository, ref, and cursor")
    if not overlay_url and any(value is not None for value in overlay_fields):
        raise ValueError("overlay collection parameters require an overlay URL")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("overlay collection limit must be between 1 and 1000")
    kinds = ["repository-validator", "release-check", "tests", "diff-check"]
    if overlay_url:
        kinds = ["source-audit", "lesson-refresh", *kinds, "qa-style-collect"]
    for position, kind in enumerate(kinds):
        parameters = {}
        if kind == "qa-style-collect":
            parameters = {
                "overlay_url": overlay_url,
                "adapter_id": adapter_id,
                "canonical_repository": canonical_repository,
                "ref": overlay_ref,
                "after_revision": after_revision,
                "limit": limit,
                "audit_sources": True,
            }
        payload = {
            "schema": SCHEMA_VERSION,
            "task": kind,
            "parameters": parameters,
            "input": context,
        }
        queue.enqueue(
            task_key=f"{plan_id}:{position:03d}:{kind}", plan_id=plan_id,
            position=position, kind=kind, payload=payload)


def render_markdown(report: dict) -> str:
    lines = [
        "# Durable maintenance queue",
        "",
        f"- Plan: `{report['plan_id']}`",
        f"- Complete: `{'yes' if report['complete'] else 'no'}`",
        f"- Blocked: `{'yes' if report['blocked'] else 'no'}`",
        "",
        "| Position | Task | State | Attempts | Input SHA-256 |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    for task in report["tasks"]:
        lines.append(
            f"| {task['position']} | `{task['kind']}` | `{task['status']}` | "
            f"{task['attempts']} | `{task['payload_sha256']}` |")
    for task in report["tasks"]:
        if task["status"] in {"pending", "running", "blocked"} and task.get("stderr"):
            lines.extend([
                "",
                f"## {task['kind']}",
                "",
                "```text",
                task["stderr"][-3000:].rstrip(),
                "```",
            ])
    return "\n".join(lines) + "\n"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", required=True, type=Path)
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    enqueue = subparsers.add_parser("enqueue")
    enqueue.add_argument("--task-key", required=True)
    enqueue.add_argument("--plan-id", required=True)
    enqueue.add_argument("--position", type=int, required=True)
    enqueue.add_argument("--kind", required=True, choices=sorted(ALLOWED_TASKS))
    enqueue.add_argument("--payload", type=Path, required=True)
    enqueue.add_argument("--max-attempts", type=int, default=3)
    default = subparsers.add_parser("enqueue-default")
    default.add_argument("--plan-id", required=True)
    default.add_argument("--context", type=Path)
    default.add_argument("--overlay-url")
    default.add_argument("--adapter-id")
    default.add_argument("--canonical-repository")
    default.add_argument("--overlay-ref")
    default.add_argument("--after-revision")
    default.add_argument("--limit", type=int, default=500)
    run = subparsers.add_parser("run")
    run.add_argument("--plan-id", required=True)
    run.add_argument("--worker", required=True)
    run.add_argument("--output-dir", required=True, type=Path)
    run.add_argument("--evidence-db", required=True, type=Path)
    run.add_argument("--until-empty", action="store_true")
    status = subparsers.add_parser("status")
    status.add_argument("--plan-id", required=True)
    status.add_argument("--output", type=Path)
    status.add_argument("--markdown-output", type=Path)
    open_plan = subparsers.add_parser("find-open-plan")
    open_plan.add_argument("--head-sha", required=True)
    open_plan.add_argument("--adapter-id", required=True)
    open_plan.add_argument("--canonical-repository", required=True)
    compact = subparsers.add_parser("compact")
    compact.add_argument("--keep-plans", type=int, default=16)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        with MaintenanceQueue(args.db) as queue:
            if args.command == "init":
                output = {"schema": SCHEMA_VERSION, "database": str(queue.path)}
            elif args.command == "enqueue":
                payload = json.loads(args.payload.read_text(encoding="utf-8"))
                output = queue.enqueue(
                    task_key=args.task_key, plan_id=args.plan_id,
                    position=args.position, kind=args.kind, payload=payload,
                    max_attempts=args.max_attempts)
            elif args.command == "enqueue-default":
                context = (json.loads(args.context.read_text(encoding="utf-8"))
                           if args.context else None)
                enqueue_default(
                    queue, args.plan_id, context, args.overlay_url,
                    args.adapter_id, args.canonical_repository,
                    args.overlay_ref, args.after_revision, args.limit)
                output = queue.status(args.plan_id)
            elif args.command == "run":
                args.output_dir.mkdir(parents=True, exist_ok=True)
                completed = []
                while True:
                    task = execute_one(
                        queue, args.plan_id, args.worker, args.output_dir,
                        args.evidence_db)
                    if task is None:
                        break
                    completed.append(task["task_key"])
                    if task["status"] == "blocked" or not args.until_empty:
                        break
                output = {**queue.status(args.plan_id), "executed": completed}
            elif args.command == "status":
                output = queue.status(args.plan_id)
                if args.output:
                    atomic_write(
                        args.output,
                        json.dumps(output, ensure_ascii=False, indent=2) + "\n")
                if args.markdown_output:
                    atomic_write(args.markdown_output, render_markdown(output))
            elif args.command == "find-open-plan":
                output = queue.find_open_plan(
                    args.head_sha, args.adapter_id,
                    args.canonical_repository)
            else:
                output = queue.compact(args.keep_plans)
    except (OSError, ValueError, RuntimeError, sqlite3.Error,
            json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if args.command not in {"run", "status"}:
        return 0
    if output.get("complete"):
        return 0
    return 1 if output.get("blocked") else 2


if __name__ == "__main__":
    raise SystemExit(main())
