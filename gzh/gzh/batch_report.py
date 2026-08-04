from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gzh.ci_observation import summarize_checks


_FULL_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.IGNORECASE)


class BatchReportConflict(RuntimeError):
    pass


class BatchReportSchemaError(ValueError):
    pass


def batch_report_digest(report: Mapping[str, Any]) -> str:
    """Hash one JSON-compatible report payload for compare-and-swap use."""
    payload = json.dumps(
        report, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sequence(value: Any, name: str) -> Sequence[Mapping[str, Any]]:
    if (not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or any(not isinstance(item, Mapping) for item in value)):
        raise BatchReportSchemaError(f"{name} must be a sequence of mappings")
    return value


def _publication_field(value: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return None


def _ref_observation(
        refs: Sequence[Mapping[str, Any]], branch: str, commit: str,
) -> dict[str, Any]:
    candidates = [ref for ref in refs if ref.get("branch") == branch]
    if not candidates:
        return {"state": "missing", "branch": branch, "sha": None,
                "url": None, "pushed_at": None, "candidates": []}
    if len(candidates) > 1:
        return {"state": "ambiguous", "branch": branch, "sha": None,
                "url": None, "pushed_at": None,
                "candidates": deepcopy(candidates)}
    candidate = candidates[0]
    sha = candidate.get("sha")
    return {
        "state": "matched" if sha == commit else "head-conflict",
        "branch": branch,
        "sha": sha,
        "url": candidate.get("url"),
        "pushed_at": _publication_field(
            candidate, "pushed_at", "pushedAt"),
        "candidates": [],
    }


def _pr_observation(
        pull_requests: Sequence[Mapping[str, Any]], branch: str, commit: str,
) -> dict[str, Any]:
    branch_matches = [
        pr for pr in pull_requests
        if _publication_field(pr, "head_branch", "headRefName") == branch
    ]
    exact = [
        pr for pr in branch_matches
        if _publication_field(pr, "head_sha", "headRefOid") == commit
    ]
    if len(exact) > 1 or (not exact and len(branch_matches) > 1):
        return {"state": "ambiguous", "candidates": deepcopy(
            exact if exact else branch_matches)}
    if not exact:
        if branch_matches:
            return {"state": "head-conflict",
                    "candidates": deepcopy(branch_matches)}
        return {"state": "missing", "candidates": []}

    raw = exact[0]
    merged_at = _publication_field(raw, "merged_at", "mergedAt")
    raw_state = str(raw.get("state") or "").lower()
    if raw.get("merged") is True or merged_at:
        state = "merged"
    elif raw_state in {"open", "closed"}:
        state = raw_state
    else:
        state = "unknown"
    checks = _sequence(raw.get("checks") or [], "pull request checks")
    rollup = summarize_checks(
        checks, complete=raw.get("checks_complete") is True)
    errors = []
    if not isinstance(raw.get("number"), int) or raw["number"] < 1:
        errors.append("pull request number is missing")
    if not isinstance(raw.get("url"), str) or not raw["url"]:
        errors.append("pull request URL is missing")
    merge_commit_sha = _publication_field(
        raw, "merge_commit_sha", "mergeCommitSha")
    if state == "merged":
        if not merged_at:
            errors.append("merge timestamp is missing")
        if (not isinstance(merge_commit_sha, str)
                or not _FULL_OID_RE.fullmatch(merge_commit_sha)):
            errors.append("merge commit SHA is missing or abbreviated")
    return {
        "state": state,
        "number": raw.get("number"),
        "url": raw.get("url"),
        "head_branch": branch,
        "head_sha": _publication_field(raw, "head_sha", "headRefOid"),
        "head_owner": _publication_field(raw, "head_owner", "headOwner"),
        "base_sha": _publication_field(raw, "base_sha", "baseRefOid"),
        "created_at": _publication_field(raw, "created_at", "createdAt"),
        "updated_at": _publication_field(raw, "updated_at", "updatedAt"),
        "merged_at": merged_at,
        "merge_commit_sha": merge_commit_sha,
        "checks": rollup,
        "complete": not errors,
        "errors": errors,
        "candidates": [],
    }


def _issue_observation(
        raw: Any, recorded_issue: Any,
) -> dict[str, Any]:
    if recorded_issue is None:
        return {"state": "unrecorded", "number": None, "url": None,
                "updated_at": None, "closed_at": None}
    if raw is None:
        return {"state": "missing", "number": recorded_issue, "url": None,
                "updated_at": None, "closed_at": None}
    if not isinstance(raw, Mapping):
        raise BatchReportSchemaError("issue observation must be a mapping")
    if raw.get("number") != recorded_issue:
        state = "mismatched"
    else:
        raw_state = str(raw.get("state") or "").lower()
        state = raw_state if raw_state in {"open", "closed"} else "incomplete"
    return {
        "state": state,
        "number": raw.get("number"),
        "url": raw.get("url"),
        "updated_at": _publication_field(raw, "updated_at", "updatedAt"),
        "closed_at": _publication_field(raw, "closed_at", "closedAt"),
    }


def _latest_check_time(checks: Sequence[Mapping[str, Any]]) -> str | None:
    timestamps = [
        check.get("completed_at") for check in checks
        if isinstance(check.get("completed_at"), str)
    ]
    return max(timestamps) if timestamps else None


def _reconcile_item(
        item: Mapping[str, Any], provider: Callable[[Mapping[str, Any]],
                                                    Mapping[str, Any]],
        observed_at: str,
) -> dict[str, Any]:
    branch = item.get("branch")
    commit = item.get("commit")
    base = {
        "item_id": item.get("id"),
        "branch": branch,
        "recorded_commit": commit,
        "observed_at": observed_at,
        "state": "incomplete",
        "reason": None,
        "branch_ref": None,
        "pull_request": None,
        "issue": None,
        "transitions": [],
    }
    if not isinstance(branch, str) or not branch:
        base["reason"] = "recorded branch is missing"
        return base
    if not isinstance(commit, str) or not _FULL_OID_RE.fullmatch(commit):
        base["reason"] = "recorded commit is missing or abbreviated"
        return base

    raw = provider(deepcopy(item))
    if not isinstance(raw, Mapping):
        raise TypeError("publication provider must return a mapping")
    refs = _sequence(raw.get("refs") or [], "publication refs")
    pull_requests = _sequence(
        raw.get("pull_requests") or [], "publication pull requests")
    branch_ref = _ref_observation(refs, branch, commit)
    pull_request = _pr_observation(pull_requests, branch, commit)
    issue = _issue_observation(raw.get("issue"), item.get("issue"))
    base.update({
        "branch_ref": branch_ref,
        "pull_request": pull_request,
        "issue": issue,
        "transitions": [{
            "state": "local_commit",
            "at": item.get("committed_at") or observed_at,
            "head_sha": commit,
        }],
    })
    if raw.get("complete") is not True:
        base["reason"] = raw.get("error") or "provider observation is incomplete"
        return base
    if branch_ref["state"] == "ambiguous" or pull_request["state"] == "ambiguous":
        base.update(state="ambiguous", reason="publication identity is ambiguous")
        return base
    if (branch_ref["state"] == "head-conflict"
            or pull_request["state"] == "head-conflict"):
        base.update(state="head-conflict",
                    reason="published head differs from the recorded commit")
        return base

    pr_matched = pull_request["state"] not in {
        "missing", "ambiguous", "head-conflict",
    }
    if branch_ref["state"] == "matched" or pr_matched:
        pushed_at = (branch_ref.get("pushed_at")
                     or pull_request.get("created_at") or observed_at)
        base["transitions"].append({
            "state": "pushed", "at": pushed_at, "head_sha": commit,
        })
    if not pr_matched:
        if branch_ref["state"] == "matched":
            base.update(state="pushed", reason="no matching pull request")
        else:
            base.update(state="missing",
                        reason="no matching branch ref or pull request")
        return base

    base["transitions"].append({
        "state": "pr_open",
        "at": pull_request.get("created_at") or observed_at,
        "head_sha": commit,
        "pr_number": pull_request.get("number"),
    })
    if pull_request["complete"] is not True:
        base.update(state="incomplete",
                    reason="pull request metadata is incomplete")
        return base
    checks = pull_request["checks"]
    if checks["state"] == "incomplete":
        base.update(state="incomplete", reason="check rollup is incomplete")
        return base
    if checks["state"] == "failed":
        base.update(state="checks-failed", reason="one or more checks failed")
        return base
    base["transitions"].append({
        "state": "checks_passed",
        "at": _latest_check_time(checks["checks"]) or observed_at,
        "head_sha": commit,
        "pr_number": pull_request.get("number"),
    })
    if pull_request["state"] == "merged":
        base["transitions"].append({
            "state": "merged",
            "at": pull_request.get("merged_at") or observed_at,
            "head_sha": commit,
            "merge_commit_sha": pull_request.get("merge_commit_sha"),
            "pr_number": pull_request.get("number"),
        })
        base.update(state="merged", reason=None)
    elif pull_request["state"] == "open":
        base.update(state="checks-passed", reason="pull request remains open")
    elif pull_request["state"] == "closed":
        base.update(state="pr-closed", reason="pull request closed without merge")
    else:
        base.update(state="incomplete", reason="pull request state is incomplete")
    return base


def reconcile_batch_report(
        report: Mapping[str, Any], *, expected_input_sha256: str,
        provider: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        observed_at: str,
) -> dict[str, Any]:
    """Add publication observations without changing original report fields."""
    current_sha256 = batch_report_digest(report)
    if not hmac.compare_digest(current_sha256, expected_input_sha256):
        raise BatchReportConflict(
            "batch report changed: expected "
            f"{expected_input_sha256}, found {current_sha256}")
    items = _sequence(report.get("items") or [], "batch report items")
    existing = report.get("publication_observations", [])
    observations = list(_sequence(
        existing, "batch report publication observations"))
    observation = {
        "schema_version": 1,
        "input_sha256": current_sha256,
        "observed_at": observed_at,
        "items": [
            _reconcile_item(item, provider, observed_at) for item in items
        ],
    }
    reconciled = deepcopy(dict(report))
    reconciled["publication_observations"] = deepcopy(observations) + [observation]
    return reconciled


def report_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_batch_report(
        directory: Path, content: str, *,
        now: datetime | None = None,
        token_hex: Callable[[int], str] = secrets.token_hex,
        suffix: str = ".md") -> Path:
    """Reserve a unique report path and durably write its first checkpoint."""
    if suffix not in {".md", ".json"}:
        raise ValueError("batch report suffix must be .md or .json")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    instant = now or datetime.now(timezone.utc)
    timestamp = instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(128):
        path = directory / f"bump-batch-{timestamp}-{token_hex(4)}{suffix}"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(directory)
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise
    raise RuntimeError("could not reserve a unique batch report path")


def checkpoint_batch_report(path: Path, content: str, *,
                            expected_sha256: str) -> str:
    """Atomically replace a report while retaining its last complete checkpoint."""
    path = Path(path)
    lock_path = path.parent / f".{path.name}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if not path.is_file() or path.is_symlink():
                raise ValueError(
                    f"batch report must be an existing regular file: {path}")
            current_sha256 = report_sha256(path)
            if current_sha256 != expected_sha256:
                raise BatchReportConflict(
                    "batch report changed: expected "
                    f"{expected_sha256}, found {current_sha256}")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                _sync_directory(path.parent)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            return report_sha256(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
