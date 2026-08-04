import json
import subprocess

import pytest

from gzh import github_observation as github_mod
from gzh.github_observation import (
    GitHubPublicationProvider,
    GitHubReadError,
    read_pull_request,
)


def _runner(responses):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        key = tuple(command)
        returncode, payload, error = responses[key]
        stdout = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(
            command, returncode, stdout=stdout, stderr=error)

    return run, calls


def _pr_command(number):
    return (
        "gh", "pr", "view", str(number), "--repo", "gentoo-zh/overlay",
        "--json",
        "number,url,state,isDraft,headRefName,headRefOid,headRepositoryOwner,"
        "baseRefOid,createdAt,updatedAt,mergedAt,mergeCommit",
    )


def _checks_command(number, cursor=None):
    command = [
        "gh", "api", "graphql", "--method", "POST",
        "-f", f"query={github_mod._CHECKS_QUERY}",
        "-F", "owner=gentoo-zh", "-F", "name=overlay",
        "-F", f"number={number}",
    ]
    if cursor is not None:
        command.extend(["-f", f"endCursor={cursor}"])
    return tuple(command)


def _checks_page(
        nodes, *, has_next=False, cursor=None, head_sha="a" * 40,
        total_count=None,
):
    if total_count is None:
        total_count = len(nodes)
    return {
        "data": {
            "repository": {
                "pullRequest": {
                    "headRefOid": head_sha,
                    "commits": {
                        "nodes": [{
                            "commit": {
                                "statusCheckRollup": {
                                    "contexts": {
                                        "totalCount": total_count,
                                        "nodes": nodes,
                                        "pageInfo": {
                                            "hasNextPage": has_next,
                                            "endCursor": cursor,
                                        },
                                    },
                                },
                            },
                        }],
                    },
                },
            },
        },
    }


def test_read_pull_request_normalizes_checks_without_shortening_names():
    check_name = "emerge (amd64-desktop-systemd, complete matrix job name)"
    runner, _calls = _runner({
        _pr_command(7): (0, {
            "number": 7,
            "url": "https://github.example/pull/7",
            "state": "MERGED",
            "isDraft": False,
            "headRefName": "cat-pkg-1",
            "headRefOid": "a" * 40,
            "headRepositoryOwner": {"login": "contributor"},
            "baseRefOid": "b" * 40,
            "createdAt": "start",
            "updatedAt": "update",
            "mergedAt": "end",
            "mergeCommit": {"oid": "c" * 40},
        }, ""),
        _checks_command(7): (0, _checks_page([{
            "__typename": "CheckRun",
            "name": check_name,
            "detailsUrl": "https://github.example/job/1",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
            "startedAt": "start",
            "completedAt": "end",
        }]), ""),
    })

    result = read_pull_request("gentoo-zh/overlay", 7, runner=runner)

    assert result["complete"] is True
    assert result["head_owner"] == "contributor"
    assert result["merge_commit_sha"] == "c" * 40
    assert result["checks"][0]["name"] == check_name
    assert result["checks_complete"] is True


def test_publication_provider_uses_only_read_queries_and_handles_missing_ref():
    user = ("gh", "api", "user")
    branch = (
        "gh", "api", "repos/contributor/overlay/git/ref/heads/cat-pkg-1")
    pulls = (
        "gh", "api", "--paginate", "--slurp", "--method", "GET",
        "repos/gentoo-zh/overlay/pulls", "-f", "state=all",
        "-f", "head=contributor:cat-pkg-1", "-f", "per_page=100")
    issue = ("gh", "api", "repos/gentoo-zh/overlay/issues/9")
    runner, calls = _runner({
        user: (0, {"login": "contributor"}, ""),
        branch: (1, "", "HTTP 404: Not Found"),
        pulls: (0, [[]], ""),
        issue: (0, {
            "number": 9, "state": "open", "html_url": "issue-url",
            "updated_at": "now", "closed_at": None,
        }, ""),
    })

    provider = GitHubPublicationProvider(
        "gentoo-zh/overlay", runner=runner)
    result = provider({"branch": "cat-pkg-1", "issue": 9})

    assert result["complete"] is True
    assert result["refs"] == []
    assert result["pull_requests"] == []
    assert result["issue"]["number"] == 9
    assert all(command[:2] in (["gh", "api"], ["gh", "pr"])
               for command in calls)


def test_explicit_fork_owner_controls_branch_and_pull_request_queries():
    branch = (
        "gh", "api", "repos/release-team/overlay-fork/git/ref/heads/cat-pkg-1")
    pulls = (
        "gh", "api", "--paginate", "--slurp", "--method", "GET",
        "repos/gentoo-zh/overlay/pulls", "-f", "state=all",
        "-f", "head=release-team:cat-pkg-1", "-f", "per_page=100")
    runner, calls = _runner({
        branch: (1, "", "HTTP 404: Not Found"),
        pulls: (0, [[]], ""),
    })

    provider = GitHubPublicationProvider(
        "gentoo-zh/overlay", fork_repository="release-team/overlay-fork",
        runner=runner)
    result = provider({"branch": "cat-pkg-1"})

    assert result["complete"] is True
    assert calls == [list(branch), list(pulls)]
    assert ["gh", "api", "user"] not in calls


def test_check_rollup_is_paginated_past_one_hundred_contexts():
    first_page = [{
        "__typename": "CheckRun",
        "name": f"check-{index:03d}",
        "detailsUrl": f"https://github.example/job/{index}",
        "status": "COMPLETED",
        "conclusion": "SUCCESS",
        "startedAt": "start",
        "completedAt": "end",
    } for index in range(100)]
    final_check = {
        "__typename": "StatusContext",
        "context": "legacy-context",
        "targetUrl": "https://github.example/status/100",
        "state": "SUCCESS",
    }
    runner, calls = _runner({
        _pr_command(8): (0, {
            "number": 8,
            "headRefOid": "a" * 40,
            "mergeCommit": None,
        }, ""),
        _checks_command(8): (
            0, _checks_page(
                first_page, has_next=True, cursor="cursor-100",
                total_count=101), ""),
        _checks_command(8, "cursor-100"): (
            0, _checks_page([final_check], total_count=101), ""),
    })

    result = read_pull_request("gentoo-zh/overlay", 8, runner=runner)

    assert result["checks_complete"] is True
    assert len(result["checks"]) == 101
    assert result["checks"][-1]["name"] == "legacy-context"
    assert _checks_command(8, "cursor-100") in [tuple(call) for call in calls]


def test_check_rollup_with_unproven_next_page_is_rejected():
    runner, _calls = _runner({
        _pr_command(9): (0, {
            "number": 9,
            "headRefOid": "a" * 40,
            "mergeCommit": None,
        }, ""),
        _checks_command(9): (0, _checks_page(
            [{"__typename": "CheckRun"}] * 100,
            has_next=True, cursor=None, total_count=101), ""),
    })

    with pytest.raises(GitHubReadError, match="omitted the next rollup cursor"):
        read_pull_request("gentoo-zh/overlay", 9, runner=runner)


def test_malformed_github_output_is_rejected():
    runner, _calls = _runner({_pr_command(1): (0, "not-json", "")})

    with pytest.raises(GitHubReadError, match="malformed JSON"):
        read_pull_request("gentoo-zh/overlay", 1, runner=runner)
