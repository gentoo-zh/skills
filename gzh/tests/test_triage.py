import json

import pytest

import gzh.triage as triage_mod
from gzh.triage import (TriageConflict, TriageCorrupt, list_skipped,
                        resolve_issue, skip_issue)


UPDATED = "2026-08-01T12:00:00Z"


def skip(log, issue, cat_pkg, version, reason, *, kind="skip",
         expected="none", updated=UPDATED):
    return skip_issue(
        log, issue, cat_pkg, version, reason, kind=kind,
        issue_updated_at=updated, expected_event_id=expected)


def test_list_empty_when_no_file(tmp_path):
    assert list_skipped(tmp_path / "skip-log.jsonl") == []


def test_skip_appends_and_list_roundtrip(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    rec = skip(log, 10588, "net-proxy/v2rayA", "2.4.6", "crash")
    assert rec["issue"] == 10588
    assert rec["cat_pkg"] == "net-proxy/v2rayA"
    assert rec["target_version"] == "2.4.6"
    assert rec["reason"] == "crash"
    assert rec["skipped_at"]  # non-empty ISO timestamp
    assert rec["issue_updated_at"] == UPDATED
    assert rec["event_id"]
    listed = list_skipped(log)
    assert len(listed) == 1
    assert listed[0]["issue"] == 10588


def test_list_filter_by_pkg(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    skip(log, 1, "a/b", "1", "r1")
    skip(log, 2, "c/d", "2", "r2")
    assert len(list_skipped(log, pkg="a/b")) == 1
    assert list_skipped(log, pkg="a/b")[0]["issue"] == 1


def test_corrupt_json_stops_read_and_write(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    log.write_text("# header comment\n{bad json\n\n", encoding="utf-8")
    before = log.read_text(encoding="utf-8")
    with pytest.raises(TriageCorrupt, match="line 2"):
        list_skipped(log)
    with pytest.raises(TriageCorrupt, match="line 2"):
        skip(log, 3, "e/f", "3", "r3")
    assert log.read_text(encoding="utf-8") == before


def test_skip_creates_parent_dir(tmp_path):
    log = tmp_path / "sub" / "nested" / "skip-log.jsonl"
    skip(log, 9, "x/y", "1", "r")
    assert log.exists()
    assert len(list_skipped(log)) == 1


def test_non_object_event_is_corrupt(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    log.write_text("42\n[1,2]\n\"str\"\n", encoding="utf-8")
    with pytest.raises(TriageCorrupt, match="line 1"):
        list_skipped(log)


def test_latest_event_supersedes_same_issue_package_and_version(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    first = skip(log, 5, "a/b", "1", "initial block")
    resolve_issue(
        log, 5, "a/b", "1", "new evidence",
        issue_updated_at="2026-08-02T12:00:00Z",
        expected_event_id=first["event_id"])
    assert list_skipped(log) == []
    assert list_skipped(log, kind="resolved")[0]["reason"] == "new evidence"
    assert len(list_skipped(log, history=True)) == 2


def test_record_identity_includes_target_version(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    skip(log, 5, "a/b", "1", "blocked")
    resolve_issue(
        log, 5, "a/b", "2", "different release",
        issue_updated_at=UPDATED, expected_event_id="none")
    assert list_skipped(log, kind="skip")[0]["target_version"] == "1"


def test_compare_and_swap_rejects_stale_concurrent_decision(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    first = skip(log, 5, "a/b", "1", "blocked")
    resolved = resolve_issue(
        log, 5, "a/b", "1", "fixed",
        issue_updated_at="2026-08-02T12:00:00Z",
        expected_event_id=first["event_id"])

    with pytest.raises(TriageConflict, match=resolved["event_id"]):
        skip(log, 5, "a/b", "1", "stale decision",
             expected=first["event_id"])

    assert list_skipped(log, kind="resolved")[0]["event_id"] == resolved["event_id"]


def test_legacy_record_gets_stable_event_id_and_recorded_at(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    legacy = {"issue": 5, "cat_pkg": "a/b", "target_version": "1",
              "reason": "old", "skipped_at": "2025-01-01T00:00:00Z"}
    log.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    first = list_skipped(log)[0]
    second = list_skipped(log)[0]
    assert first["recorded_at"] == legacy["skipped_at"]
    assert first["event_id"].startswith("legacy-")
    assert first["event_id"] == second["event_id"]


def test_skip_requires_timezone_in_issue_revision(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        skip(tmp_path / "skip-log.jsonl", 1, "a/b", "1", "bad",
             updated="2026-08-01T12:00:00")


def test_older_issue_revision_cannot_supersede_current_event(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    first = skip(log, 1, "a/b", "1", "current",
                 updated="2026-08-02T12:00:00Z")

    with pytest.raises(TriageConflict, match="older"):
        resolve_issue(
            log, 1, "a/b", "1", "stale resolution",
            issue_updated_at="2026-08-01T12:00:00Z",
            expected_event_id=first["event_id"])


def test_failed_atomic_replace_retains_complete_triage_log(tmp_path, monkeypatch):
    log = tmp_path / "skip-log.jsonl"
    first = skip(log, 1, "a/b", "1", "first")
    before = log.read_text(encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr(triage_mod.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        resolve_issue(
            log, 1, "a/b", "1", "second", issue_updated_at=UPDATED,
            expected_event_id=first["event_id"])

    assert log.read_text(encoding="utf-8") == before
    assert list(log.parent.glob("*.tmp")) == []


from click.testing import CliRunner

from gzh.cli import cli


def test_triage_list_help_registered():
    result = CliRunner().invoke(cli, ["triage", "list", "--help"])
    assert result.exit_code == 0


def test_triage_skip_and_list_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    revisions = iter((UPDATED, UPDATED,
                      "2026-08-02T12:00:00Z"))
    monkeypatch.setattr(
        cli_mod, "get_issue_updated_at", lambda repo, issue: next(revisions))
    r1 = CliRunner().invoke(cli_mod.cli,
                            ["triage", "skip", "100",
                             "--cat-pkg", "a/b", "--target-version", "1.0",
                             "--issue-updated-at", UPDATED,
                             "--expected-event-id", "none",
                             "--reason", "testing"])
    assert r1.exit_code == 0
    import json as _json
    assert _json.loads(r1.output)["issue"] == 100
    r2 = CliRunner().invoke(cli_mod.cli, ["triage", "list"])
    assert r2.exit_code == 0
    listed = _json.loads(r2.output)
    assert len(listed) == 1
    assert listed[0]["cat_pkg"] == "a/b"
    assert (tmp_path / "triage" / "skip-log.jsonl").exists()

    r3 = CliRunner().invoke(cli_mod.cli,
                            ["triage", "resolve", "100",
                             "--cat-pkg", "a/b", "--target-version", "1.0",
                             "--issue-updated-at", "2026-08-02T12:00:00Z",
                             "--expected-event-id", listed[0]["event_id"],
                             "--reason", "superseded"])
    assert r3.exit_code == 0
    assert _json.loads(r3.output)["kind"] == "resolved"
    r4 = CliRunner().invoke(cli_mod.cli, ["triage", "list"])
    assert _json.loads(r4.output) == []


def test_cli_refuses_stale_github_issue_revision(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        cli_mod, "get_issue_updated_at",
        lambda repo, issue: "2026-08-02T12:00:00Z")

    result = CliRunner().invoke(cli_mod.cli, [
        "triage", "skip", "100", "--cat-pkg", "a/b",
        "--target-version", "1.0", "--issue-updated-at", UPDATED,
        "--expected-event-id", "none", "--reason", "stale"])

    assert result.exit_code == 1
    assert "issue changed" in result.output
    assert not (tmp_path / "triage" / "skip-log.jsonl").exists()


def test_cli_deactivates_skip_when_issue_changes_during_write(
        tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    revisions = iter((UPDATED, "2026-08-02T12:00:00Z"))
    monkeypatch.setattr(
        cli_mod, "get_issue_updated_at", lambda repo, issue: next(revisions))

    result = CliRunner().invoke(cli_mod.cli, [
        "triage", "skip", "100", "--cat-pkg", "a/b",
        "--target-version", "1.0", "--issue-updated-at", UPDATED,
        "--expected-event-id", "none", "--reason", "raced"])

    assert result.exit_code == 1
    assert "deactivated" in result.output
    log = tmp_path / "triage" / "skip-log.jsonl"
    assert list_skipped(log) == []
    assert list_skipped(log, kind="resolved")[0]["reason"].startswith(
        "Issue revision")
