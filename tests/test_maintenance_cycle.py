from __future__ import annotations

import importlib.util
import json
import sys
import time
from types import SimpleNamespace

import pytest


def load_module():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    path = (root / ".agents" / "skills" / "gzh-maintain-skills"
            / "scripts" / "maintenance_cycle.py")
    spec = importlib.util.spec_from_file_location("maintenance_cycle_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


maintenance = load_module()


def test_github_slug_accepts_canonical_forms():
    assert maintenance.github_slug("git@github.com:gentoo-zh/skills.git") == \
        "gentoo-zh/skills"
    assert maintenance.github_slug("https://github.com/gentoo-zh/skills.git") == \
        "gentoo-zh/skills"
    assert maintenance.github_slug("https://example.com/gentoo-zh/skills") is None


def test_source_summary_preserves_attention_ids():
    step = {"stdout": """[
      {"id": "current", "state": "current"},
      {"id": "changed", "state": "drift"},
      {"id": "offline", "state": "error"}
    ]"""}
    assert maintenance.source_summary(step) == {
        "total": 3,
        "states": {"current": 1, "drift": 1, "error": 1},
        "attention": ["changed", "offline"],
    }


def test_required_baseline_rejects_dirty_checkout(monkeypatch):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": "master",
        "dirty": True,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 0,
        "fetch": None,
    })
    monkeypatch.setattr(maintenance, "run_command", lambda name, command, timeout=900: {
        "name": name,
        "command": command,
        "ok": True,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout": "",
        "stderr": "",
    })
    args = SimpleNamespace(
        fetch=False, require_synced_master=True, skip_network=True,
        skip_tests=True)

    report = maintenance.collect(args)

    assert report["ok"] is False
    gate = next(step for step in report["steps"]
                if step["name"] == "repository-state")
    assert gate["ok"] is False


@pytest.mark.parametrize(("allow_detached", "expected"), [
    (False, False),
    (True, True),
])
def test_required_baseline_allows_only_explicit_synced_detached_head(
        monkeypatch, allow_detached, expected):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": None,
        "dirty": False,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 0,
        "fetch": None,
    })
    monkeypatch.setattr(maintenance, "run_command", lambda name, command,
                        timeout=900: {
        "name": name,
        "command": command,
        "ok": True,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout": "",
        "stderr": "",
        "timed_out": False,
        "truncated": False,
    })
    args = SimpleNamespace(
        fetch=False, require_synced_master=True,
        allow_detached_head=allow_detached, skip_network=True,
        skip_tests=True)

    report = maintenance.collect(args)

    gate = next(step for step in report["steps"]
                if step["name"] == "repository-state")
    assert gate["ok"] is expected


def test_required_baseline_rejects_detached_head_behind_canonical(monkeypatch):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": None,
        "dirty": False,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 1,
        "fetch": None,
    })
    args = SimpleNamespace(
        fetch=False, require_synced_master=True, allow_detached_head=True,
        skip_network=True, skip_tests=True)

    report = maintenance.collect(args)

    gate = next(step for step in report["steps"]
                if step["name"] == "repository-state")
    assert gate["ok"] is False


def test_timeout_bytes_remain_serializable():
    result = maintenance.run_command(
        "slow",
        [sys.executable, "-c", (
            "import os,time; os.write(1,b'partial\\xff'); "
            "os.close(1); os.close(2); time.sleep(30)")],
        timeout=0.5,
        max_output_bytes=1024,
    )

    assert result["ok"] is False
    assert result["stdout"] == "partial\ufffd"
    assert result["stdout_bytes"] == 8
    assert result["timed_out"] is True
    assert result["truncated"] is True
    assert "timed out after 0.5 seconds" in result["stderr"]


def test_noisy_child_is_bounded_and_process_group_is_stopped(tmp_path):
    marker = tmp_path / "descendant-survived"
    descendant = (
        "import pathlib,sys,time; time.sleep(0.5); "
        "pathlib.Path(sys.argv[1]).write_text('alive')")
    parent = (
        "import os,subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[2],sys.argv[1]]); "
        "os.write(1,b'x'*4096); time.sleep(30)")

    result = maintenance.run_command(
        "noisy", [sys.executable, "-c", parent, str(marker), descendant],
        timeout=5,
        max_output_bytes=128,
    )
    time.sleep(0.7)

    assert result["ok"] is False
    assert result["returncode"] is None
    assert result["stdout"] == "x" * 128
    assert result["stdout_bytes"] == 128
    assert result["stderr_bytes"] == 0
    assert result["timed_out"] is False
    assert result["truncated"] is True
    assert "output exceeded 128 bytes" in result["stderr"]
    assert not marker.exists()


def test_checked_output_uses_the_git_output_limit(monkeypatch):
    monkeypatch.setattr(maintenance, "MAX_GIT_OUTPUT_BYTES", 64)

    with pytest.raises(maintenance.BoundedProcessError) as raised:
        maintenance.checked_output([
            sys.executable, "-c", "import os; os.write(1,b'x'*4096)"])

    assert raised.value.stdout == b"x" * 64
    assert raised.value.timed_out is False
    assert raised.value.truncated is True


def test_repository_timeout_is_explicit_without_captured_output(monkeypatch):
    def timeout(fetch):
        raise maintenance.BoundedProcessError(
            "timed out after 30 seconds", stdout=b"sensitive partial output",
            stderr=b"", timed_out=True)

    monkeypatch.setattr(maintenance, "repository_state", timeout)
    args = SimpleNamespace(
        fetch=False, require_synced_master=False, skip_network=False,
        skip_tests=False)

    report = maintenance.collect(args)

    assert report["ok"] is False
    assert report["truncated"] is True
    assert report["repository_timed_out"] is True
    assert report["repository_truncated"] is True
    assert "sensitive partial output" not in json.dumps(report)


@pytest.mark.parametrize(("skip_network", "skip_tests", "skipped"), [
    (True, False, ["source-audit", "lesson-refresh"]),
    (False, True, ["tests"]),
    (True, True, ["source-audit", "lesson-refresh", "tests"]),
])
def test_skipped_gates_do_not_claim_a_complete_cycle(
        monkeypatch, skip_network, skip_tests, skipped):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": "master",
        "dirty": False,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 0,
        "fetch": None,
    })

    def pass_command(name, command, timeout=900):
        return {
            "name": name,
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_seconds": 0.0,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(maintenance, "run_command", pass_command)
    args = SimpleNamespace(
        fetch=False, require_synced_master=False, skip_network=skip_network,
        skip_tests=skip_tests)

    report = maintenance.collect(args)

    assert report["ok"] is True
    assert report["complete"] is False
    assert set(report["requested_gates"]) | set(report["skipped_gates"]) == {
        "source-audit", "lesson-refresh", "repository-validator",
        "release-check", "static-evals", "tests", "compile-check",
        "diff-check"}
    assert report["skipped_gates"] == skipped
    steps = {step["name"]: step for step in report["steps"]}
    assert steps["static-evals"]["command"] == [
        sys.executable, str(maintenance.EVAL_RUNNER), "static"]
    assert steps["compile-check"]["command"] == [
        sys.executable, "-m", "compileall", "-q", "gzh/gzh", "scripts",
        ".agents/skills"]
    assert "Result: scoped pass" in maintenance.render_markdown(report)
    assert "Skipped gates:" in maintenance.render_markdown(report)


@pytest.mark.parametrize(("failed_gate", "expected_calls"), [
    ("static-evals", [
        "repository-validator", "release-check", "static-evals"]),
    ("compile-check", [
        "repository-validator", "release-check", "static-evals",
        "compile-check"]),
])
def test_required_static_gate_failure_stops_cycle(
        monkeypatch, failed_gate, expected_calls):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": "master",
        "dirty": False,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 0,
        "fetch": None,
    })
    calls = []

    def run(name, command, timeout=900):
        calls.append(name)
        return {
            "name": name,
            "command": command,
            "ok": name != failed_gate,
            "returncode": 1 if name == failed_gate else 0,
            "duration_seconds": 0.0,
            "stdout": "",
            "stderr": "failed" if name == failed_gate else "",
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(maintenance, "run_command", run)
    args = SimpleNamespace(
        fetch=False, require_synced_master=False, skip_network=True,
        skip_tests=True)

    report = maintenance.collect(args)

    assert report["ok"] is False
    assert calls == expected_calls
    assert report["steps"][-1]["name"] == failed_gate


def test_markdown_report_lists_failed_gate():
    report = {
        "ok": False,
        "repository": {
            "head": "abc", "branch": "master", "canonical_remote": "origin",
            "ahead": 0, "behind": 0, "dirty": False,
        },
        "steps": [{
            "name": "tests", "ok": False, "duration_seconds": 1.25,
            "stderr": "one failure\n", "stdout": "",
        }],
        "sources": {"total": 40, "states": {"current": 39, "drift": 1},
                    "attention": ["official-source"]},
    }

    text = maintenance.render_markdown(report)

    assert "review required" in text
    assert "`official-source`" in text
    assert "one failure" in text


def test_failed_fetch_stops_before_other_gates(monkeypatch):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": "master",
        "dirty": False,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 0,
        "fetch": {
            "name": "fetch", "command": ["git", "fetch"], "ok": False,
            "returncode": 1, "duration_seconds": 0.1, "stdout": "",
            "stderr": "network unavailable",
        },
    })
    calls = []
    monkeypatch.setattr(
        maintenance, "run_command",
        lambda name, command, timeout=900: calls.append(name))
    args = SimpleNamespace(
        fetch=True, require_synced_master=True, skip_network=False,
        skip_tests=False)

    report = maintenance.collect(args)

    assert report["ok"] is False
    assert [step["name"] for step in report["steps"]] == ["fetch"]
    assert calls == []


def test_failed_source_audit_stops_queue(monkeypatch):
    monkeypatch.setattr(maintenance, "repository_state", lambda fetch: {
        "head": "a" * 40,
        "branch": "master",
        "dirty": False,
        "canonical_remote": "origin",
        "canonical_url": "git@github.com:gentoo-zh/skills.git",
        "ahead": 0,
        "behind": 0,
        "fetch": None,
    })
    calls = []

    def run(name, command, timeout=900):
        calls.append(name)
        return {
            "name": name, "command": command, "ok": False,
            "returncode": 1, "duration_seconds": 0.1,
            "stdout": '[{"id":"changed","state":"drift"}]',
            "stderr": "source drift",
        }

    monkeypatch.setattr(maintenance, "run_command", run)
    args = SimpleNamespace(
        fetch=False, require_synced_master=False, skip_network=False,
        skip_tests=False)

    report = maintenance.collect(args)

    assert calls == ["source-audit"]
    assert report["sources"]["attention"] == ["changed"]
    assert report["ok"] is False


def test_markdown_includes_repository_discovery_error():
    report = {
        "ok": False,
        "repository": {},
        "repository_error": "expected one canonical remote; found none",
        "steps": [],
        "sources": None,
    }

    text = maintenance.render_markdown(report)

    assert "Repository discovery" in text
    assert "found none" in text


def test_main_persists_complete_report_in_evidence_database(
        tmp_path, monkeypatch, capsys):
    report = {
        "schema": 1,
        "generated_at": "2026-08-04T00:00:00Z",
        "repository": {},
        "steps": [],
        "sources": None,
        "ok": True,
        "complete": True,
        "truncated": False,
    }
    output = tmp_path / "report.json"
    database = tmp_path / "evidence.db"
    monkeypatch.setattr(maintenance, "collect", lambda args: dict(report))
    monkeypatch.setattr(sys, "argv", [
        "maintenance_cycle.py", "--output", str(output),
        "--evidence-db", str(database),
    ])

    assert maintenance.main() == 0

    written = json.loads(output.read_text())
    evidence_step = next(
        step for step in written["steps"] if step["name"] == "evidence-store")
    assert evidence_step["ok"] is True
    assert database.is_file()
    stdout = capsys.readouterr().out
    assert stdout == f"Wrote complete maintenance report to {output}.\n"
    assert '"schema"' not in stdout
