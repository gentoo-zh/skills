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
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_UTC_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?Z")
_PACKAGE_RE = re.compile(r"[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+")
BATCH_REPORT_SCHEMA_VERSION = 2
BATCH_OUTCOME_STATES = (
    "pending",
    "blocked",
    "local_committed",
    "superseded_by_external_merge",
    "pushed",
    "pr_open",
    "checks_passed",
    "merged",
)
BATCH_UPDATE_STATES = BATCH_OUTCOME_STATES[1:]
_OUTCOME_TRANSITIONS = {
    None: frozenset({"pending"}),
    "pending": frozenset({
        "blocked", "local_committed", "superseded_by_external_merge",
    }),
    "blocked": frozenset({
        "local_committed", "superseded_by_external_merge",
    }),
    "local_committed": frozenset({
        "pushed", "superseded_by_external_merge",
    }),
    "pushed": frozenset({"pr_open", "superseded_by_external_merge"}),
    "pr_open": frozenset({
        "checks_passed", "superseded_by_external_merge",
    }),
    "checks_passed": frozenset({
        "merged", "superseded_by_external_merge",
    }),
    "superseded_by_external_merge": frozenset(),
    "merged": frozenset(),
}


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


def _plain_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise BatchReportSchemaError(f"{name} must be an array")
    return value


def _nonempty_string(value: Any, name: str, *, maximum: int = 4096) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value.encode("utf-8")) > maximum
            or any(ord(character) < 32 for character in value)):
        raise BatchReportSchemaError(f"{name} must be a bounded non-empty string")
    return value


def _valid_utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def _validate_outcome(
        outcome: Any, item_id: str, *, selection_snapshot_sha256: str) -> None:
    if not isinstance(outcome, Mapping):
        raise BatchReportSchemaError(f"item {item_id!r} outcome must be a mapping")
    state = outcome.get("state")
    if state not in BATCH_OUTCOME_STATES:
        raise BatchReportSchemaError(f"item {item_id!r} has an invalid outcome state")
    transitions = _sequence(
        outcome.get("transitions"), f"item {item_id!r} outcome transitions")
    if not transitions:
        raise BatchReportSchemaError(
            f"item {item_id!r} outcome transitions must not be empty")
    previous = None
    for index, transition in enumerate(transitions):
        source = transition.get("from_state")
        target = transition.get("state")
        if source != previous:
            raise BatchReportSchemaError(
                f"item {item_id!r} transition {index} has a discontinuous source")
        if target not in _OUTCOME_TRANSITIONS.get(source, frozenset()):
            raise BatchReportSchemaError(
                f"item {item_id!r} transition {index} is not allowed")
        if not _valid_utc_timestamp(transition.get("at")):
            raise BatchReportSchemaError(
                f"item {item_id!r} transition {index} has an invalid UTC time")
        _nonempty_string(
            transition.get("reason"),
            f"item {item_id!r} transition {index} reason")
        evidence = transition.get("evidence")
        if not isinstance(evidence, Mapping):
            raise BatchReportSchemaError(
                f"item {item_id!r} transition {index} evidence must be a mapping")
        if (index == 0
                and evidence.get("selection_snapshot_sha256")
                != selection_snapshot_sha256):
            raise BatchReportSchemaError(
                f"item {item_id!r} pending transition must bind the selection snapshot")
        previous = target
    if previous != state:
        raise BatchReportSchemaError(
            f"item {item_id!r} outcome state does not match its transitions")


def validate_batch_report(report: Any) -> None:
    """Validate the durable structured batch-report schema."""
    if not isinstance(report, Mapping):
        raise BatchReportSchemaError("batch report must be a mapping")
    if report.get("schema_version") != BATCH_REPORT_SCHEMA_VERSION:
        raise BatchReportSchemaError(
            f"batch report schema_version must be {BATCH_REPORT_SCHEMA_VERSION}")
    _nonempty_string(report.get("batch_id"), "batch_id", maximum=256)
    if not _valid_utc_timestamp(report.get("created_at")):
        raise BatchReportSchemaError("created_at must be a UTC timestamp ending in Z")

    snapshot = report.get("selection_snapshot")
    if not isinstance(snapshot, Mapping):
        raise BatchReportSchemaError("selection_snapshot must be a mapping")
    _nonempty_string(snapshot.get("path"), "selection_snapshot.path")
    if not _SHA256_RE.fullmatch(snapshot.get("sha256") or ""):
        raise BatchReportSchemaError("selection_snapshot.sha256 must be a SHA-256 digest")
    if snapshot.get("schema_version") != 2:
        raise BatchReportSchemaError("selection_snapshot.schema_version must be 2")
    selection_expression = snapshot.get("selection_expression")
    if not isinstance(selection_expression, Mapping):
        raise BatchReportSchemaError(
            "selection_snapshot.selection_expression must be a mapping")
    resulting_issues = _plain_list(
        snapshot.get("resulting_issues"),
        "selection_snapshot.resulting_issues")
    if (any(not isinstance(issue, int) or isinstance(issue, bool) or issue < 1
            for issue in resulting_issues)
            or len(resulting_issues) != len(set(resulting_issues))):
        raise BatchReportSchemaError(
            "selection_snapshot.resulting_issues must contain unique issue numbers")
    issue_mode = selection_expression.get("issue_mode")
    explicit_issues = selection_expression.get("explicit_issues")
    queue_expression = selection_expression.get("queue")
    if (issue_mode not in {"include", "exact"}
            or selection_expression.get("composition") != (
                "explicit_only" if issue_mode == "exact"
                else "filtered_queue_or_explicit")
            or not isinstance(queue_expression, Mapping)
            or not isinstance(explicit_issues, list)
            or any(not isinstance(issue, int) or isinstance(issue, bool) or issue < 1
                   for issue in explicit_issues)
            or len(explicit_issues) != len(set(explicit_issues))):
        raise BatchReportSchemaError(
            "selection_snapshot.selection_expression is incomplete")
    _nonempty_string(
        queue_expression.get("repository"),
        "selection_snapshot queue repository", maximum=256)
    if (queue_expression.get("evaluated") != (issue_mode == "include")
            or queue_expression.get("label") != "nvchecker"
            or queue_expression.get("state") not in {"open", "closed", "all"}
            or not isinstance(queue_expression.get("limit"), int)
            or isinstance(queue_expression.get("limit"), bool)
            or not 1 <= queue_expression["limit"] <= 1000
            or (queue_expression.get("maintainer") is not None
                and not isinstance(queue_expression.get("maintainer"), str))
            or (queue_expression.get("package") is not None
                and not isinstance(queue_expression.get("package"), str))
            or queue_expression.get("autobump") not in {
                "any", "off", "on", "manual-required"}):
        raise BatchReportSchemaError(
            "selection_snapshot queue expression is incomplete")
    if issue_mode == "exact" and resulting_issues != explicit_issues:
        raise BatchReportSchemaError(
            "exact selection_snapshot issues must match its expression")

    items = _sequence(report.get("items"), "batch report items")
    item_ids = set()
    item_issues = set()
    for item in items:
        item_id = _nonempty_string(item.get("id"), "item.id", maximum=256)
        issue = item.get("issue")
        package = item.get("package")
        _nonempty_string(item.get("target_version"), f"item {item_id!r} target_version")
        if item_id in item_ids:
            raise BatchReportSchemaError(f"duplicate batch item id: {item_id}")
        if not isinstance(issue, int) or isinstance(issue, bool) or issue < 1:
            raise BatchReportSchemaError(
                f"item {item_id!r} must reference one positive issue number")
        if issue in item_issues:
            raise BatchReportSchemaError(f"duplicate batch item issue: {issue}")
        if not isinstance(package, str) or not _PACKAGE_RE.fullmatch(package):
            raise BatchReportSchemaError(
                f"item {item_id!r} package must be category/package")
        _validate_outcome(
            item.get("outcome"), item_id,
            selection_snapshot_sha256=snapshot["sha256"])
        if item.get("branch") is not None:
            _nonempty_string(item["branch"], f"item {item_id!r} branch")
        if (item.get("commit") is not None
                and not _FULL_OID_RE.fullmatch(item["commit"] or "")):
            raise BatchReportSchemaError(
                f"item {item_id!r} commit must be a full object ID")
        state = item["outcome"]["state"]
        if state in {
                "local_committed", "pushed", "pr_open", "checks_passed", "merged"}:
            _nonempty_string(item.get("branch"), f"item {item_id!r} branch")
            if not _FULL_OID_RE.fullmatch(item.get("commit") or ""):
                raise BatchReportSchemaError(
                    f"item {item_id!r} commit must be a full object ID")
        item_ids.add(item_id)
        item_issues.add(issue)

    if item_issues != set(resulting_issues):
        raise BatchReportSchemaError(
            "batch item issues must exactly match selection_snapshot.resulting_issues")

    for name in ("failures", "skips", "escalations"):
        if name in report:
            _sequence(report[name], name)
    for name in ("checks_skipped", "warnings", "residual_risks"):
        if name in report:
            _plain_list(report[name], name)
    if "publication_observations" in report:
        _sequence(report["publication_observations"], "publication_observations")


def update_batch_outcome(
        report: Mapping[str, Any], *, expected_input_sha256: str,
        item_id: str, state: str, at: str, reason: str,
        evidence: Mapping[str, Any], branch: str | None = None,
        commit: str | None = None,
) -> dict[str, Any]:
    """Append one validated item outcome transition without rewriting evidence."""
    validate_batch_report(report)
    current_sha256 = batch_report_digest(report)
    if not hmac.compare_digest(current_sha256, expected_input_sha256):
        raise BatchReportConflict(
            "batch report changed: expected "
            f"{expected_input_sha256}, found {current_sha256}")
    _nonempty_string(item_id, "item_id", maximum=256)
    if state not in BATCH_UPDATE_STATES:
        raise BatchReportSchemaError(f"invalid batch outcome update state: {state!r}")
    if not _valid_utc_timestamp(at):
        raise BatchReportSchemaError("outcome transition time must be UTC and end in Z")
    _nonempty_string(reason, "outcome transition reason")
    if not isinstance(evidence, Mapping):
        raise BatchReportSchemaError("outcome transition evidence must be a mapping")

    updated = deepcopy(dict(report))
    matches = [item for item in updated["items"] if item.get("id") == item_id]
    if len(matches) != 1:
        raise BatchReportSchemaError(
            f"batch report must contain exactly one item with id {item_id!r}")
    item = matches[0]
    outcome = item["outcome"]
    source = outcome["state"]
    if state not in _OUTCOME_TRANSITIONS[source]:
        raise BatchReportSchemaError(
            f"batch outcome transition {source!r} -> {state!r} is not allowed")
    if branch is not None:
        _nonempty_string(branch, "outcome transition branch")
        if item.get("branch") not in {None, branch}:
            raise BatchReportSchemaError("outcome transition branch conflicts with report")
        item["branch"] = branch
    if commit is not None:
        if not _FULL_OID_RE.fullmatch(commit):
            raise BatchReportSchemaError("outcome transition commit must be a full object ID")
        if item.get("commit") not in {None, commit}:
            raise BatchReportSchemaError("outcome transition commit conflicts with report")
        item["commit"] = commit
    outcome["transitions"].append({
        "from_state": source,
        "state": state,
        "at": at,
        "reason": reason,
        "evidence": deepcopy(dict(evidence)),
    })
    outcome["state"] = state
    validate_batch_report(updated)
    return updated


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
    validate_batch_report(report)
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
    validate_batch_report(reconciled)
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
