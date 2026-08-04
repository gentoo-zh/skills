import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import gzh.batch_report as batch_report
import gzh.cli as cli_mod
from gzh.batch_report import (BatchReportConflict, BatchReportSchemaError,
                              batch_report_digest, checkpoint_batch_report,
                              create_batch_report, report_sha256,
                              update_batch_outcome, validate_batch_report)


NOW = datetime(2026, 8, 4, 1, 2, 3, tzinfo=timezone.utc)


def _structured_report(*, items=None):
    selected_issues = [] if items is None else [item["issue"] for item in items]
    return {
        "schema_version": 2,
        "batch_id": "fixture-batch",
        "created_at": "2026-08-04T01:02:03Z",
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
                "explicit_issues": selected_issues,
            },
            "resulting_issues": selected_issues,
        },
        "items": [] if items is None else items,
        "custom_evidence": {"retain": [1, 2, 3]},
    }


def _pending_item(*, item_id="cat/pkg@1", issue=9, package="cat/pkg",
                  target_version="1"):
    return {
        "id": item_id,
        "issue": issue,
        "package": package,
        "target_version": target_version,
        "outcome": {
            "state": "pending",
            "transitions": [{
                "from_state": None,
                "state": "pending",
                "at": "2026-08-04T01:02:03Z",
                "reason": "The queue snapshot selected this item for processing.",
                "evidence": {"selection_snapshot_sha256": "f" * 64},
            }],
        },
    }


def _local_item():
    item = _pending_item()
    item.update({
        "branch": "cat-pkg-1",
        "commit": "a" * 40,
        "qa": {"commands": ["gzh qa"], "unknown_field": True},
    })
    item["outcome"]["state"] = "local_committed"
    item["outcome"]["transitions"].append({
        "from_state": "pending",
        "state": "local_committed",
        "at": "2026-08-04T01:10:00Z",
        "reason": "The verified package change is committed locally.",
        "evidence": {"commit": "a" * 40},
    })
    return item


def test_create_reserves_unique_report_names(tmp_path):
    first = create_batch_report(
        tmp_path, "first\n", now=NOW, token_hex=lambda _: "deadbeef")
    tokens = iter(("deadbeef", "cafebabe"))
    second = create_batch_report(
        tmp_path, "second\n", now=NOW, token_hex=lambda _: next(tokens))

    assert first.name == "bump-batch-20260804T010203Z-deadbeef.md"
    assert second.name == "bump-batch-20260804T010203Z-cafebabe.md"
    assert first.read_text(encoding="utf-8") == "first\n"
    assert second.read_text(encoding="utf-8") == "second\n"


def test_checkpoint_replaces_complete_report(tmp_path):
    report = create_batch_report(tmp_path, "old\n", now=NOW,
                                 token_hex=lambda _: "deadbeef")
    digest = checkpoint_batch_report(
        report, "new complete checkpoint\n",
        expected_sha256=report_sha256(report))
    assert report.read_text(encoding="utf-8") == "new complete checkpoint\n"
    assert digest == report_sha256(report)
    assert list(tmp_path.glob("*.tmp")) == []


def test_failed_replace_retains_previous_checkpoint(tmp_path, monkeypatch):
    report = create_batch_report(tmp_path, "old\n", now=NOW,
                                 token_hex=lambda _: "deadbeef")

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(batch_report.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        checkpoint_batch_report(
            report, "incomplete\n", expected_sha256=report_sha256(report))

    assert report.read_text(encoding="utf-8") == "old\n"
    assert list(tmp_path.glob("*.tmp")) == []


def test_stale_checkpoint_cannot_overwrite_concurrent_result(tmp_path):
    report = create_batch_report(tmp_path, "old\n", now=NOW,
                                 token_hex=lambda _: "deadbeef")
    original = report_sha256(report)
    checkpoint_batch_report(report, "delegate one\n", expected_sha256=original)

    with pytest.raises(BatchReportConflict, match="changed"):
        checkpoint_batch_report(
            report, "delegate two stale view\n", expected_sha256=original)

    assert report.read_text(encoding="utf-8") == "delegate one\n"


def test_batch_report_cli_create_and_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    runner = CliRunner()
    created = runner.invoke(
        cli_mod.cli, ["batch-report", "create"], input="# Initial\n")
    assert created.exit_code == 0, created.output
    created_data = json.loads(created.output)
    report = tmp_path / "batches" / Path(created_data["path"]).name
    assert report.read_text(encoding="utf-8") == "# Initial\n"

    updated = runner.invoke(
        cli_mod.cli, ["batch-report", "checkpoint", str(report),
                      "--expected-sha256", created_data["sha256"]],
        input="# Updated\n")
    assert updated.exit_code == 0, updated.output
    assert report.read_text(encoding="utf-8") == "# Updated\n"
    assert json.loads(updated.output)["sha256"] == report_sha256(report)


def test_batch_report_cli_rejects_report_outside_state(tmp_path, monkeypatch):
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path / "state"))
    outside = tmp_path / "bump-batch-outside.md"
    outside.write_text("keep\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli_mod.cli, ["batch-report", "checkpoint", str(outside),
                      "--expected-sha256", report_sha256(outside)],
        input="replace\n")

    assert result.exit_code == 1
    assert outside.read_text(encoding="utf-8") == "keep\n"


def test_batch_report_cli_creates_multi_item_pending_json_checkpoint(
        tmp_path, monkeypatch):
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    value = _structured_report(items=[
        _pending_item(),
        _pending_item(
            item_id="cat/other@2", issue=10, package="cat/other",
            target_version="2"),
    ])
    result = CliRunner().invoke(
        cli_mod.cli, ["batch-report", "create", "--format", "json"],
        input=json.dumps(value))

    assert result.exit_code == 0, result.output
    created = json.loads(result.output)
    report = Path(created["path"])
    assert report.suffix == ".json"
    assert json.loads(report.read_text(encoding="utf-8")) == value
    assert created["sha256"] == report_sha256(report)
    assert [item["outcome"]["state"] for item in value["items"]] == [
        "pending", "pending"]


def test_batch_report_cli_reconciles_json_through_cas(tmp_path, monkeypatch):
    state = tmp_path / "state"
    batches = state / "batches"
    batches.mkdir(parents=True)
    report = batches / "bump-batch-fixture.json"
    report.write_text(json.dumps(_structured_report(items=[_local_item()])),
                      encoding="utf-8")
    monkeypatch.setenv("GZH_STATE_DIR", str(state))

    class Provider:
        def __init__(self, repository, fork_repository=None):
            assert repository == "gentoo-zh/overlay"
            assert fork_repository is None

        def __call__(self, item):
            return {
                "complete": True,
                "refs": [{"branch": item["branch"], "sha": item["commit"]}],
                "pull_requests": [],
                "issue": {"number": 9, "state": "open"},
            }

    monkeypatch.setattr(cli_mod, "GitHubPublicationProvider", Provider)
    result = CliRunner().invoke(cli_mod.cli, [
        "batch-report", "reconcile", str(report),
        "--expected-sha256", report_sha256(report),
    ])

    assert result.exit_code == 0, result.output
    output = json.loads(result.output)
    assert output["observation"]["items"][0]["state"] == "pushed"
    assert output["sha256"] == report_sha256(report)
    assert len(json.loads(report.read_text())["publication_observations"]) == 1


def test_structured_schema_rejects_unknown_version_and_discontinuous_transition():
    report = _structured_report(items=[_local_item()])
    report["schema_version"] = 7
    with pytest.raises(BatchReportSchemaError, match="schema_version"):
        validate_batch_report(report)

    report = _structured_report(items=[_local_item()])
    report["items"][0]["outcome"]["transitions"][0]["from_state"] = "blocked"
    with pytest.raises(BatchReportSchemaError, match="discontinuous"):
        validate_batch_report(report)

    report = _structured_report(items=[_local_item()])
    report["created_at"] = "2026-99-04T01:02:03Z"
    with pytest.raises(BatchReportSchemaError, match="created_at"):
        validate_batch_report(report)


def test_structured_schema_requires_pending_as_initial_transition():
    report = _structured_report(items=[_local_item()])
    report["items"][0]["outcome"]["transitions"] = [{
        "from_state": None,
        "state": "local_committed",
        "at": "2026-08-04T01:10:00Z",
        "reason": "This attempts to omit the pending selection state.",
        "evidence": {"commit": "a" * 40},
    }]

    with pytest.raises(BatchReportSchemaError, match="not allowed"):
        validate_batch_report(report)


def test_pending_transition_must_bind_the_selection_snapshot():
    report = _structured_report(items=[_pending_item()])
    report["items"][0]["outcome"]["transitions"][0]["evidence"] = {
        "selection_snapshot_sha256": "0" * 64,
    }

    with pytest.raises(BatchReportSchemaError, match="bind the selection snapshot"):
        validate_batch_report(report)


def test_structured_schema_rejects_missing_selected_issue_item():
    report = _structured_report(items=[_local_item()])
    report["items"] = []

    with pytest.raises(BatchReportSchemaError, match="exactly match"):
        validate_batch_report(report)


def test_structured_schema_rejects_item_issue_outside_selection():
    report = _structured_report(items=[_local_item()])
    report["items"][0]["issue"] = 10

    with pytest.raises(BatchReportSchemaError, match="exactly match"):
        validate_batch_report(report)


def test_typed_outcome_update_preserves_original_and_unknown_evidence():
    report = _structured_report(items=[_local_item()])
    original = json.loads(json.dumps(report))
    updated = update_batch_outcome(
        report,
        expected_input_sha256=batch_report_digest(report),
        item_id="cat/pkg@1",
        state="pushed",
        at="2026-08-04T02:00:00Z",
        reason="The recorded commit is present on the exact fork branch.",
        evidence={"ref": "refs/heads/cat-pkg-1", "sha": "a" * 40},
    )

    assert report == original
    assert updated["custom_evidence"] == original["custom_evidence"]
    assert updated["items"][0]["qa"] == original["items"][0]["qa"]
    assert updated["items"][0]["outcome"]["state"] == "pushed"
    assert updated["items"][0]["outcome"]["transitions"][-1]["from_state"] == \
        "local_committed"


def test_typed_outcome_update_rejects_skipped_state():
    report = _structured_report(items=[_local_item()])
    with pytest.raises(BatchReportSchemaError, match="not allowed"):
        update_batch_outcome(
            report,
            expected_input_sha256=batch_report_digest(report),
            item_id="cat/pkg@1",
            state="merged",
            at="2026-08-04T02:00:00Z",
            reason="The transition attempts to skip publication evidence.",
            evidence={},
        )


def test_pending_item_rejects_skipped_classification_transition():
    report = _structured_report(items=[_pending_item()])
    with pytest.raises(BatchReportSchemaError, match="not allowed"):
        update_batch_outcome(
            report,
            expected_input_sha256=batch_report_digest(report),
            item_id="cat/pkg@1",
            state="pushed",
            at="2026-08-04T02:00:00Z",
            reason="The transition attempts to skip package classification.",
            evidence={},
        )


@pytest.mark.parametrize(
    ("state", "branch", "commit"),
    [
        ("blocked", None, None),
        ("local_committed", "cat-pkg-1", "a" * 40),
        ("superseded_by_external_merge", None, None),
    ],
)
def test_pending_item_accepts_each_classification_transition(
        state, branch, commit):
    report = _structured_report(items=[_pending_item()])
    updated = update_batch_outcome(
        report,
        expected_input_sha256=batch_report_digest(report),
        item_id="cat/pkg@1",
        state=state,
        at="2026-08-04T02:00:00Z",
        reason="The item now has a complete evidence-based classification.",
        evidence={"classification": state},
        branch=branch,
        commit=commit,
    )

    assert updated["items"][0]["outcome"]["state"] == state
    assert updated["items"][0]["outcome"]["transitions"][-1][
        "from_state"] == "pending"


def test_pending_is_not_an_update_target():
    report = _structured_report(items=[_pending_item()])
    with pytest.raises(BatchReportSchemaError, match="update state"):
        update_batch_outcome(
            report,
            expected_input_sha256=batch_report_digest(report),
            item_id="cat/pkg@1",
            state="pending",
            at="2026-08-04T02:00:00Z",
            reason="The transition attempts to repeat the initial state.",
            evidence={},
        )


def test_blocked_item_can_record_identity_when_it_becomes_local_committed():
    item = _pending_item()
    item["outcome"]["state"] = "blocked"
    item["outcome"]["transitions"].append({
        "from_state": "pending",
        "state": "blocked",
        "at": "2026-08-04T01:10:00Z",
        "reason": "Required upstream evidence was unavailable.",
        "evidence": {"source": "release"},
    })
    report = _structured_report(items=[item])

    updated = update_batch_outcome(
        report,
        expected_input_sha256=batch_report_digest(report),
        item_id="cat/pkg@1",
        state="local_committed",
        at="2026-08-04T02:00:00Z",
        reason="The missing evidence is now verified and the change is committed.",
        evidence={"source_sha256": "b" * 64},
        branch="cat-pkg-1",
        commit="a" * 40,
    )

    assert updated["items"][0]["branch"] == "cat-pkg-1"
    assert updated["items"][0]["commit"] == "a" * 40


def test_batch_report_cli_updates_one_outcome_through_cas(tmp_path, monkeypatch):
    state = tmp_path / "state"
    batches = state / "batches"
    batches.mkdir(parents=True)
    report = batches / "bump-batch-fixture.json"
    value = _structured_report(items=[_local_item()])
    report.write_text(json.dumps(value), encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"ref_sha": "a" * 40}), encoding="utf-8")
    monkeypatch.setenv("GZH_STATE_DIR", str(state))

    result = CliRunner().invoke(cli_mod.cli, [
        "batch-report", "update", str(report),
        "--expected-sha256", report_sha256(report),
        "--item-id", "cat/pkg@1",
        "--state", "pushed",
        "--at", "2026-08-04T02:00:00Z",
        "--reason", "The exact recorded commit is present on the fork branch.",
        "--evidence", str(evidence),
    ])

    assert result.exit_code == 0, result.output
    stored = json.loads(report.read_text(encoding="utf-8"))
    assert stored["items"][0]["outcome"]["state"] == "pushed"
    assert stored["custom_evidence"] == value["custom_evidence"]
    assert json.loads(result.output)["sha256"] == report_sha256(report)


def test_batch_reconciliation_rejects_stale_file_before_provider(tmp_path, monkeypatch):
    state = tmp_path / "state"
    batches = state / "batches"
    batches.mkdir(parents=True)
    report = batches / "bump-batch-fixture.json"
    report.write_text('{"items": []}\n', encoding="utf-8")
    monkeypatch.setenv("GZH_STATE_DIR", str(state))
    calls = []
    monkeypatch.setattr(
        cli_mod, "GitHubPublicationProvider",
        lambda *args, **kwargs: calls.append((args, kwargs)))

    result = CliRunner().invoke(cli_mod.cli, [
        "batch-report", "reconcile", str(report),
        "--expected-sha256", "0" * 64,
    ])

    assert result.exit_code == 1
    assert "batch report changed" in result.output
    assert calls == []
