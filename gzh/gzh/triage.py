from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path


# A skip is a persistent exclusion. An escalation is provisional and must be revisited
# when the missing evidence or maintainer decision becomes available.
ACTIVE_KINDS = ("skip", "escalate")
EXPECT_ABSENT = "none"


class TriageConflict(RuntimeError):
    pass


class TriageCorrupt(RuntimeError):
    pass


def _normalise_record(record: dict, index: int) -> dict:
    record = dict(record)
    if "recorded_at" not in record and record.get("skipped_at"):
        record["recorded_at"] = record["skipped_at"]
    if "event_id" not in record:
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{index}:{payload}".encode()).hexdigest()[:16]
        record["event_id"] = f"legacy-{digest}"
    return record


def _decode_records(lines, *, strict: bool = False) -> list[dict]:
    records = []
    for index, line in enumerate(lines):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if strict:
                raise TriageCorrupt(
                    f"invalid JSON in triage log line {index + 1}") from exc
            continue
        if isinstance(record, dict):
            records.append(_normalise_record(record, index))
        elif strict:
            raise TriageCorrupt(
                f"non-object event in triage log line {index + 1}")
    return records


def _lock_path(log_path: Path) -> Path:
    return log_path.parent / f".{log_path.name}.lock"


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_records(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    with _lock_path(log_path).open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            if not log_path.exists():
                return []
            return _decode_records(
                log_path.read_text(encoding="utf-8").splitlines(), strict=True)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _key(record: dict) -> tuple:
    return (record.get("issue"), record.get("cat_pkg"),
            record.get("target_version"))


def list_skipped(log_path: Path, pkg: str | None = None,
                 kind: str | None = None, history: bool = False) -> list[dict]:
    log_path = Path(log_path)
    records = _read_records(log_path)
    if not history:
        latest = {}
        for index, record in enumerate(records):
            latest[_key(record)] = (index, record)
        records = [record for _, record in sorted(latest.values())]
    out = []
    for rec in records:
        if pkg and rec.get("cat_pkg") != pkg:
            continue
        record_kind = rec.get("kind", "skip")
        if kind and record_kind != kind:
            continue
        if not history and kind is None and record_kind == "resolved":
            continue
        out.append(rec)
    return out


def skip_issue(log_path: Path, issue: int, cat_pkg: str,
               target_version: str, reason: str, *, issue_updated_at: str,
               expected_event_id: str, kind: str = "skip") -> dict:
    if kind not in ACTIVE_KINDS:
        raise ValueError(f"invalid active triage kind: {kind}")
    return _append_record(
        log_path, issue, cat_pkg, target_version, reason, kind,
        issue_updated_at, expected_event_id)


def resolve_issue(log_path: Path, issue: int, cat_pkg: str,
                  target_version: str, reason: str, *, issue_updated_at: str,
                  expected_event_id: str) -> dict:
    return _append_record(
        log_path, issue, cat_pkg, target_version, reason, "resolved",
        issue_updated_at, expected_event_id)


def _timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid issue updated_at timestamp: {value}") from exc
    if parsed.tzinfo is None:
        raise ValueError("issue updated_at timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _append_record(log_path: Path, issue: int, cat_pkg: str,
                   target_version: str, reason: str, kind: str,
                   issue_updated_at: str, expected_event_id: str) -> dict:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    recorded_at = datetime.now(timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")
    rec = {"issue": issue, "cat_pkg": cat_pkg, "target_version": target_version,
           "reason": reason, "kind": kind, "recorded_at": recorded_at,
           "issue_updated_at": _timestamp(issue_updated_at),
           "event_id": secrets.token_hex(16)}
    if kind in ACTIVE_KINDS:
        rec["skipped_at"] = recorded_at
    with _lock_path(log_path).open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            existing = (log_path.read_text(encoding="utf-8")
                        if log_path.exists() else "")
            records = _decode_records(existing.splitlines(), strict=True)
            current = next(
                (record for record in reversed(records) if _key(record) == _key(rec)),
                None)
            current_id = current.get("event_id") if current else EXPECT_ABSENT
            if expected_event_id != current_id:
                raise TriageConflict(
                    f"triage state changed: expected {expected_event_id}, "
                    f"found {current_id}")
            if (current and current.get("issue_updated_at")
                    and rec["issue_updated_at"] < current["issue_updated_at"]):
                raise TriageConflict(
                    "issue snapshot is older than the current triage event: "
                    f"{rec['issue_updated_at']} < {current['issue_updated_at']}")
            separator = "" if not existing or existing.endswith("\n") else "\n"
            content = existing + separator + json.dumps(rec, ensure_ascii=False) + "\n"
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{log_path.name}.", suffix=".tmp", dir=log_path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, log_path)
                _sync_directory(log_path.parent)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return rec
