from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any


def _mapping_sequence(value: Any, name: str) -> Sequence[Mapping[str, Any]]:
    if (not isinstance(value, Sequence)
            or isinstance(value, (str, bytes))
            or any(not isinstance(item, Mapping) for item in value)):
        raise TypeError(f"{name} must be a sequence of mappings")
    return value


def analyze_cleanup_dry_run(
        worktrees: Sequence[Mapping[str, Any]],
        publication_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Classify cleanup candidates without changing branches or worktrees."""
    worktrees = _mapping_sequence(worktrees, "worktrees")
    publication_items = _mapping_sequence(
        publication_items, "publication items")
    records: dict[str, list[Mapping[str, Any]]] = {}
    for item in publication_items:
        branch = item.get("branch")
        if isinstance(branch, str) and branch:
            records.setdefault(branch, []).append(item)

    results = []
    for worktree in worktrees:
        branch = worktree.get("branch")
        head_sha = worktree.get("head_sha")
        reasons = []
        detached = worktree.get("detached") is True or not branch
        if detached:
            reasons.append("detached")
            matched = None
        else:
            matches = records.get(branch, [])
            matched = matches[0] if len(matches) == 1 else None
            if not matches:
                reasons.append("unrecorded")
            elif len(matches) > 1:
                reasons.extend(("unrecorded", "ambiguous-record"))

        if worktree.get("dirty") is not False:
            reasons.append("dirty")
        if matched is not None:
            recorded_commit = matched.get("recorded_commit")
            pull_request = matched.get("pull_request")
            if not isinstance(pull_request, Mapping):
                pull_request = {}
            branch_ref = matched.get("branch_ref")
            if not isinstance(branch_ref, Mapping):
                branch_ref = {}
            pr_head_sha = pull_request.get("head_sha")
            if (not recorded_commit or head_sha != recorded_commit
                    or pr_head_sha != recorded_commit
                    or branch_ref.get("state") in {"head-conflict", "ambiguous"}
                    or (branch_ref.get("state") == "matched"
                        and branch_ref.get("sha") != recorded_commit)):
                reasons.append("mismatched")
            if matched.get("state") != "merged" or pull_request.get("state") != "merged":
                reasons.append("unmerged")
        ahead = worktree.get("unpushed_commits", worktree.get("ahead"))
        if not isinstance(ahead, int) or isinstance(ahead, bool) or ahead != 0:
            reasons.append("unpushed")

        reasons = list(dict.fromkeys(reasons))
        results.append({
            "path": worktree.get("path"),
            "branch": branch,
            "head_sha": head_sha,
            "candidate": not reasons,
            "reasons": reasons,
        })

    results.sort(key=lambda item: str(item["path"] or ""))
    return {
        "schema_version": 1,
        "dry_run": True,
        "candidate_count": sum(item["candidate"] for item in results),
        "rejected_count": sum(not item["candidate"] for item in results),
        "worktrees": deepcopy(results),
        "actions": [],
    }
