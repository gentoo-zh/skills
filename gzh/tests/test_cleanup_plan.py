from copy import deepcopy

import pytest

from gzh.cleanup_plan import analyze_cleanup_dry_run


COMMIT = "a" * 40


def _publication(**overrides):
    item = {
        "branch": "app-misc-demo-1.2.3",
        "recorded_commit": COMMIT,
        "state": "merged",
        "pull_request": {
            "state": "merged",
            "head_sha": COMMIT,
            "number": 456,
        },
    }
    item.update(overrides)
    return item


def _worktree(**overrides):
    item = {
        "path": "/var/tmp/app-misc-demo-1.2.3",
        "branch": "app-misc-demo-1.2.3",
        "head_sha": COMMIT,
        "detached": False,
        "dirty": False,
        "unpushed_commits": 0,
    }
    item.update(overrides)
    return item


def test_clean_merged_exact_worktree_is_only_a_dry_run_candidate():
    worktrees = [_worktree()]
    publications = [_publication()]
    original_worktrees = deepcopy(worktrees)
    original_publications = deepcopy(publications)

    result = analyze_cleanup_dry_run(worktrees, publications)

    assert result["dry_run"] is True
    assert result["actions"] == []
    assert result["candidate_count"] == 1
    assert result["worktrees"][0]["candidate"] is True
    assert result["worktrees"][0]["reasons"] == []
    assert worktrees == original_worktrees
    assert publications == original_publications


@pytest.mark.parametrize(("worktree_change", "publication_change", "reason"), [
    ({"dirty": True}, {}, "dirty"),
    ({}, {"state": "checks-passed"}, "unmerged"),
    ({"head_sha": "b" * 40}, {}, "mismatched"),
    ({"detached": True, "branch": None}, {}, "detached"),
    ({"branch": "unrecorded-topic"}, {}, "unrecorded"),
    ({"unpushed_commits": 1}, {}, "unpushed"),
])
def test_cleanup_rejects_each_unsafe_worktree(
        worktree_change, publication_change, reason):
    result = analyze_cleanup_dry_run(
        [_worktree(**worktree_change)], [_publication(**publication_change)])
    worktree = result["worktrees"][0]
    assert worktree["candidate"] is False
    assert reason in worktree["reasons"]
    assert result["actions"] == []


def test_cleanup_rejects_pr_head_mismatch():
    publication = _publication(pull_request={
        "state": "merged", "head_sha": "c" * 40, "number": 456})
    result = analyze_cleanup_dry_run([_worktree()], [publication])
    assert result["worktrees"][0]["candidate"] is False
    assert "mismatched" in result["worktrees"][0]["reasons"]


def test_missing_status_and_ahead_evidence_fail_closed():
    worktree = _worktree()
    del worktree["dirty"]
    del worktree["unpushed_commits"]
    result = analyze_cleanup_dry_run([worktree], [_publication()])
    assert result["worktrees"][0]["reasons"] == ["dirty", "unpushed"]
