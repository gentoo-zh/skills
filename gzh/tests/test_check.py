import subprocess
from pathlib import Path

from gzh.check import Gate, run_read_only_checks


def _result(*, ok=True, complete=True, truncated=False):
    return {"ok": ok, "complete": complete, "truncated": truncated}


def test_complete_failures_do_not_hide_later_gate_evidence(tmp_path):
    called = []

    def gate(name, result):
        def run(root: Path):
            assert root == tmp_path.resolve()
            called.append(name)
            return result
        return run

    report = run_read_only_checks(tmp_path, [
        Gate("lint", gate("lint", _result(ok=False))),
        Gate("qa", gate("qa", _result())),
        Gate("artifacts", runner=None, required=False,
             skip_reason="artifact evidence was not requested"),
    ])

    assert called == ["lint", "qa"]
    assert report["complete"] is True
    assert report["ok"] is False
    assert report["state"] == "failed"
    assert report["gates"][0]["state"] == "failed"
    assert report["gates"][2]["skipped"] is True
    assert report["gates"][2]["skip_reason"] == "artifact evidence was not requested"


def test_incomplete_gate_stops_and_blocks_remaining_gates(tmp_path):
    called = []

    def incomplete(_root):
        called.append("qa")
        return _result(ok=False, complete=False, truncated=True)

    def must_not_run(_root):
        called.append("binary")
        return _result()

    report = run_read_only_checks(tmp_path, [
        Gate("qa", incomplete),
        Gate("binary", must_not_run),
    ])

    assert called == ["qa"]
    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["gates"][0]["state"] == "incomplete"
    assert report["gates"][0]["complete"] is False
    assert report["gates"][1]["skip_reason"] == "blocked by incomplete gate: qa"


def test_required_skip_is_incomplete_and_stops(tmp_path):
    report = run_read_only_checks(tmp_path, [
        Gate("doctor", runner=None, skip_reason="adapter is unavailable"),
        Gate("lint", lambda _root: _result()),
    ])

    assert report["complete"] is False
    assert report["gates"][0]["state"] == "skipped"
    assert report["gates"][1]["state"] == "skipped"
    assert "required gate was skipped" in report["errors"][0]["message"]


def test_mutating_or_unknown_gate_is_rejected_before_execution(tmp_path):
    called = []

    def runner(_root):
        called.append(True)
        return _result()

    for name in ("manifest", "push", "custom-shell"):
        report = run_read_only_checks(tmp_path, [Gate(name, runner)])
        assert report["complete"] is False
        assert report["gates"][0]["skip_reason"] == "invalid gate configuration"

    assert called == []


def test_empty_gate_set_and_missing_root_cannot_report_green(tmp_path):
    empty = run_read_only_checks(tmp_path, [])
    assert empty["complete"] is False
    assert "at least one" in empty["errors"][0]["message"]

    missing = run_read_only_checks(
        tmp_path / "missing", [Gate("qa", lambda _root: _result())])
    assert missing["complete"] is False
    assert "existing directory" in missing["errors"][0]["message"]


def test_exception_and_malformed_result_are_incomplete(tmp_path):
    def raises(_root):
        raise RuntimeError("collector failed")

    raised = run_read_only_checks(tmp_path, [Gate("qa", raises)])
    assert raised["complete"] is False
    assert raised["errors"][0]["type"] == "RuntimeError"

    malformed = run_read_only_checks(
        tmp_path, [Gate("qa", lambda _root: {"ok": True})])
    assert malformed["complete"] is False
    assert malformed["gates"][0]["state"] == "incomplete"


def test_gate_that_changes_git_input_is_rejected(tmp_path):
    subprocess.run(["git", "init", "-q", tmp_path], check=True)
    subprocess.run(
        ["git", "-C", tmp_path, "config", "user.email", "test@example.invalid"],
        check=True)
    subprocess.run(
        ["git", "-C", tmp_path, "config", "user.name", "Test"], check=True)
    tracked = tmp_path / "tracked"
    tracked.write_text("before\n")
    subprocess.run(["git", "-C", tmp_path, "add", "tracked"], check=True)
    subprocess.run(
        ["git", "-C", tmp_path, "commit", "-qm", "fixture"], check=True)

    def mutates(_root):
        tracked.write_text("after\n")
        return _result()

    report = run_read_only_checks(tmp_path, [Gate("qa", mutates)])

    assert report["complete"] is False
    assert report["gates"][0]["state"] == "incomplete"
    assert report["errors"][0]["stage"] == "read-only-boundary"
