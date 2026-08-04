from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
import re
from typing import Any


_PASSING_CONCLUSIONS = {"SUCCESS"}
_NEUTRAL_CONCLUSIONS = {"NEUTRAL"}
_SKIPPED_CONCLUSIONS = {"SKIPPED"}
_CANCELLED_CONCLUSIONS = {"CANCELLED"}
_FAILING_CONCLUSIONS = {
    "ACTION_REQUIRED",
    "FAILURE",
    "STALE",
    "STARTUP_FAILURE",
    "TIMED_OUT",
}
_COUNT_STATES = (
    "passed", "pending", "failed", "cancelled", "neutral", "skipped",
    "unknown",
)
_FULL_OID_RE = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}", re.IGNORECASE)


def _field(value: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in value:
            return value[name]
    return None


def _check_state(status: str, conclusion: str | None) -> str:
    if status != "COMPLETED" or conclusion is None:
        return "pending"
    if conclusion in _PASSING_CONCLUSIONS:
        return "passed"
    if conclusion in _NEUTRAL_CONCLUSIONS:
        return "neutral"
    if conclusion in _SKIPPED_CONCLUSIONS:
        return "skipped"
    if conclusion in _CANCELLED_CONCLUSIONS:
        return "cancelled"
    if conclusion in _FAILING_CONCLUSIONS:
        return "failed"
    return "unknown"


def summarize_checks(
        checks: Sequence[Mapping[str, Any]], *, complete: bool = True,
) -> dict[str, Any]:
    """Normalize a complete, non-truncated check rollup without shortening fields."""
    normalized = []
    errors = []
    for index, raw in enumerate(checks):
        name = _field(raw, "name", "context")
        url = _field(raw, "url", "details_url", "detailsUrl")
        status = str(_field(raw, "status") or "").upper()
        conclusion_value = _field(raw, "conclusion")
        conclusion = (
            str(conclusion_value).upper() if conclusion_value is not None else None)
        state = _check_state(status, conclusion)
        if not isinstance(name, str) or not name:
            errors.append(f"check {index} has no complete name")
        if not isinstance(url, str) or not url:
            errors.append(f"check {index} has no URL")
        normalized.append({
            "name": name,
            "url": url,
            "status": status or None,
            "conclusion": conclusion,
            "state": state,
            "started_at": _field(raw, "started_at", "startedAt"),
            "completed_at": _field(raw, "completed_at", "completedAt"),
        })

    normalized.sort(key=lambda check: (
        str(check["name"] or ""), str(check["url"] or ""),
        str(check["status"] or ""), str(check["conclusion"] or ""),
    ))
    state_counts = Counter(check["state"] for check in normalized)
    counts = {"total": len(normalized)}
    counts.update({state: state_counts[state] for state in _COUNT_STATES})

    complete = bool(complete) and not errors
    if not complete or not normalized or counts["pending"] or counts["unknown"]:
        state = "incomplete"
    elif counts["failed"] or counts["cancelled"]:
        state = "failed"
    else:
        state = "passed"
    return {
        "state": state,
        "complete": complete,
        "counts": counts,
        "checks": normalized,
        "errors": errors,
    }


def _final_pr_state(raw: Mapping[str, Any]) -> str:
    if raw.get("merged") is True or _field(raw, "merged_at", "mergedAt"):
        return "merged"
    state = str(raw.get("state") or "").lower()
    return state if state in {"open", "closed"} else "unknown"


def observe_ci(
        repository: str, pr_number: int,
        provider: Callable[[str, int], Mapping[str, Any]], *,
        observed_at: str,
) -> dict[str, Any]:
    """Return one structured CI snapshot from an injected read-only provider."""
    if not repository or "/" not in repository:
        raise ValueError("repository must use owner/name form")
    if int(pr_number) < 1:
        raise ValueError("pull request number must be positive")
    raw = provider(repository, int(pr_number))
    if not isinstance(raw, Mapping):
        raise TypeError("CI provider must return a mapping")

    raw_checks = raw.get("checks") or []
    if (not isinstance(raw_checks, Sequence)
            or isinstance(raw_checks, (str, bytes))):
        raise TypeError("CI provider checks must be a sequence")
    checks = summarize_checks(
        raw_checks, complete=raw.get("checks_complete") is True)
    head_sha = _field(raw, "head_sha", "headSha")
    pr_url = raw.get("url")
    errors = list(checks["errors"])
    if not isinstance(head_sha, str) or not _FULL_OID_RE.fullmatch(head_sha):
        errors.append("pull request has no full head SHA")
    if not isinstance(pr_url, str) or not pr_url:
        errors.append("pull request has no URL")
    final_pr_state = _final_pr_state(raw)
    merge_commit_sha = _field(raw, "merge_commit_sha", "mergeCommitSha")
    merged_at = _field(raw, "merged_at", "mergedAt")
    if final_pr_state == "unknown":
        errors.append("pull request state is incomplete")
    if final_pr_state == "merged":
        if not merged_at:
            errors.append("merged pull request has no merge timestamp")
        if (not isinstance(merge_commit_sha, str)
                or not _FULL_OID_RE.fullmatch(merge_commit_sha)):
            errors.append("merged pull request has no full merge commit SHA")
    provider_complete = raw.get("complete") is True
    return {
        "schema_version": 1,
        "repository": repository,
        "pr_number": int(pr_number),
        "observed_at": observed_at,
        "head_sha": head_sha,
        "pr_url": pr_url,
        "final_pr_state": final_pr_state,
        "is_draft": raw.get("is_draft", raw.get("isDraft")) is True,
        "merged_at": merged_at,
        "merge_commit_sha": merge_commit_sha,
        "checks_state": checks["state"],
        "counts": deepcopy(checks["counts"]),
        "checks": deepcopy(checks["checks"]),
        "complete": provider_complete and checks["complete"] and not errors,
        "errors": errors,
    }
