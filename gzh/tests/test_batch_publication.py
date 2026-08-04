from copy import deepcopy

import pytest

from gzh.batch_report import (
    BatchReportConflict,
    batch_report_digest,
    reconcile_batch_report,
)


COMMIT = "a" * 40
MERGE_COMMIT = "b" * 40
OBSERVED_AT = "2026-08-04T03:00:00Z"


def _report():
    return {
        "schema_version": 2,
        "batch_id": "fixture-batch",
        "created_at": "2026-08-04T01:00:00Z",
        "selection_snapshot": {
            "path": "/state/queues/bump-issues.json",
            "sha256": "f" * 64,
            "schema_version": 2,
            "selection_expression": {
                "issue_mode": "exact",
                "composition": "explicit_only",
                "queue": {
                    "evaluated": False,
                    "repository": "gentoo-zh/overlay",
                    "label": "nvchecker",
                    "state": "open",
                    "limit": 100,
                    "maintainer": None,
                    "package": None,
                    "autobump": "any",
                },
                "explicit_issues": [123],
            },
            "resulting_issues": [123],
        },
        "items": [{
            "id": "app-misc/demo@1.2.3",
            "issue": 123,
            "package": "app-misc/demo",
            "target_version": "1.2.3",
            "branch": "app-misc-demo-1.2.3",
            "commit": COMMIT,
            "committed_at": "2026-08-04T01:00:00Z",
            "outcome": {
                "state": "local_committed",
                "transitions": [
                    {
                        "from_state": None,
                        "state": "pending",
                        "at": "2026-08-04T00:30:00Z",
                        "reason": "The queue snapshot selected this item for processing.",
                        "evidence": {"selection_snapshot_sha256": "f" * 64},
                    },
                    {
                        "from_state": "pending",
                        "state": "local_committed",
                        "at": "2026-08-04T01:00:00Z",
                        "reason": "The verified change is committed locally.",
                        "evidence": {"commit": COMMIT},
                    },
                ],
            },
            "qa": {"state": "passed", "commands": ["gzh qa"]},
        }],
        "skips": [{"issue": 99, "reason": "dependency unavailable"}],
        "failures": [{"issue": 100, "attempts": 2}],
        "qa_results": {"raw": "retain this value exactly\n"},
        "residual_risks": ["arm64 was not executed"],
    }


def _merged_provider(item):
    assert item["branch"] == "app-misc-demo-1.2.3"
    return {
        "complete": True,
        "refs": [],
        "pull_requests": [{
            "number": 456,
            "url": "https://github.example/pull/456",
            "head_branch": item["branch"],
            "head_sha": item["commit"],
            "base_sha": "c" * 40,
            "state": "MERGED",
            "created_at": "2026-08-04T01:30:00Z",
            "merged_at": "2026-08-04T02:30:00Z",
            "merge_commit_sha": MERGE_COMMIT,
            "checks_complete": True,
            "checks": [{
                "name": "emerge (amd64-desktop-systemd, complete job name)",
                "url": "https://github.example/checks/1000000000000000001",
                "status": "COMPLETED",
                "conclusion": "SUCCESS",
                "completed_at": "2026-08-04T02:00:00Z",
            }],
        }],
        "issue": {
            "number": 123,
            "state": "CLOSED",
            "url": "https://github.example/issues/123",
            "updated_at": "2026-08-04T02:31:00Z",
            "closed_at": "2026-08-04T02:31:00Z",
        },
    }


def _reconcile(report, provider):
    return reconcile_batch_report(
        report,
        expected_input_sha256=batch_report_digest(report),
        provider=provider,
        observed_at=OBSERVED_AT,
    )


def test_locally_complete_item_reconciles_to_merged_without_branch_ref():
    report = _report()
    original = deepcopy(report)

    result = _reconcile(report, _merged_provider)

    assert report == original
    for field in original:
        assert result[field] == original[field]
    observation = result["publication_observations"][0]
    item = observation["items"][0]
    assert observation["input_sha256"] == batch_report_digest(original)
    assert item["state"] == "merged"
    assert item["branch_ref"]["state"] == "missing"
    assert item["pull_request"]["head_sha"] == COMMIT
    assert item["pull_request"]["merge_commit_sha"] == MERGE_COMMIT
    assert item["pull_request"]["checks"]["checks"][0]["name"].endswith(
        "complete job name)")
    assert item["issue"]["state"] == "closed"
    assert [transition["state"] for transition in item["transitions"]] == [
        "local_commit", "pushed", "pr_open", "checks_passed", "merged",
    ]
    assert all(transition["at"] for transition in item["transitions"])
    assert all(transition["head_sha"] == COMMIT
               for transition in item["transitions"])


def test_stale_input_digest_fails_before_provider_is_called():
    report = _report()
    calls = []

    with pytest.raises(BatchReportConflict, match="batch report changed"):
        reconcile_batch_report(
            report,
            expected_input_sha256="0" * 64,
            provider=lambda item: calls.append(item),
            observed_at=OBSERVED_AT,
        )

    assert calls == []
    assert "publication_observations" not in report


def test_pull_request_with_changed_head_is_a_conflict():
    def provider(item):
        data = _merged_provider(item)
        data["refs"] = [{"branch": item["branch"], "sha": item["commit"]}]
        data["pull_requests"][0]["head_sha"] = "d" * 40
        return data

    item = _reconcile(_report(), provider)["publication_observations"][0][
        "items"][0]
    assert item["state"] == "head-conflict"
    assert item["pull_request"]["state"] == "head-conflict"


def test_missing_publication_is_explicit():
    item = _reconcile(
        _report(), lambda _item: {
            "complete": True, "refs": [], "pull_requests": []},
    )["publication_observations"][0]["items"][0]
    assert item["state"] == "missing"
    assert item["branch_ref"]["state"] == "missing"
    assert item["pull_request"]["state"] == "missing"
    assert item["issue"]["state"] == "missing"


def test_duplicate_exact_branch_refs_are_ambiguous():
    def provider(item):
        return {
            "complete": True,
            "refs": [
                {"branch": item["branch"], "sha": item["commit"],
                 "url": "https://github.example/fork-one"},
                {"branch": item["branch"], "sha": item["commit"],
                 "url": "https://github.example/fork-two"},
            ],
            "pull_requests": [],
        }

    item = _reconcile(_report(), provider)["publication_observations"][0][
        "items"][0]
    assert item["state"] == "ambiguous"
    assert item["branch_ref"]["state"] == "ambiguous"
    assert len(item["branch_ref"]["candidates"]) == 2


def test_pending_checks_remain_incomplete_even_when_pr_is_merged():
    def provider(item):
        data = _merged_provider(item)
        check = data["pull_requests"][0]["checks"][0]
        check.update(status="IN_PROGRESS", conclusion=None, completed_at=None)
        return data

    item = _reconcile(_report(), provider)["publication_observations"][0][
        "items"][0]
    assert item["state"] == "incomplete"
    assert item["pull_request"]["state"] == "merged"
    assert item["pull_request"]["checks"]["state"] == "incomplete"


def test_matching_ref_without_pr_is_pushed_nonterminal():
    def provider(item):
        return {
            "complete": True,
            "refs": [{
                "branch": item["branch"],
                "sha": item["commit"],
                "pushed_at": "2026-08-04T01:20:00Z",
            }],
            "pull_requests": [],
            "issue": {"number": 123, "state": "OPEN"},
        }

    item = _reconcile(_report(), provider)["publication_observations"][0][
        "items"][0]
    assert item["state"] == "pushed"
    assert item["issue"]["state"] == "open"
    assert item["transitions"][-1] == {
        "state": "pushed",
        "at": "2026-08-04T01:20:00Z",
        "head_sha": COMMIT,
    }
