import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import gzh.batch_report as batch_report
import gzh.cli as cli_mod
from gzh.batch_report import (BatchReportConflict, checkpoint_batch_report,
                              create_batch_report, report_sha256)


NOW = datetime(2026, 8, 4, 1, 2, 3, tzinfo=timezone.utc)


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
