import os
import subprocess
import sys
from pathlib import Path

from gzh.pkgcheck import SEVERITIES, run_pkgcheck
from gzh.qa_evidence import run_evidence_command

# pkgcheck's JsonStream reporter emits NDJSON: one flat object per line, keyword name in
# __class__, and NO severity field. The pass/fail gate is pkgcheck's own --exit status.


def test_uses_jsonstream_reporter_and_exit_level(tmp_path):
    seen = {}

    def fake(args, **kw):
        seen["args"] = args
        out = ('{"__class__": "UnquotedVariable", "category": "dev-python", '
               '"package": "foo", "version": "1.0"}\n')
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    res = run_pkgcheck(tmp_path, min_severity="error", runner=fake)
    assert "-R" in seen["args"] and "JsonStream" in seen["args"]
    assert seen["args"][seen["args"].index("--exit") + 1] == "error"
    assert res["ok"] is True  # fake returncode 0
    assert res["results"][0]["code"] == "UnquotedVariable"  # __class__ mirrored to code


def test_ok_follows_pkgcheck_exit_status(tmp_path):
    def fake(args, **kw):
        # pkgcheck --exit returns non-zero when a gated finding exists
        return subprocess.CompletedProcess(
            args, 1,
            stdout='{"__class__":"NonexistentDeps","category":"x","package":"y","version":"1"}\n',
            stderr="")

    assert run_pkgcheck(tmp_path, runner=fake)["ok"] is False


def test_clean_scan(tmp_path):
    def fake(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_pkgcheck(tmp_path, runner=fake)
    assert res["ok"] is True
    assert res["complete"] is False
    assert res["errors"][0]["stage"] == "tool-version"
    assert res["results"] == []


def test_min_severity_maps_to_exit_with_fallback(tmp_path):
    seen = {}

    def fake(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_pkgcheck(tmp_path, min_severity="warning", runner=fake)
    assert seen["args"][seen["args"].index("--exit") + 1] == "warning"
    run_pkgcheck(tmp_path, min_severity="bogus", runner=fake)  # invalid -> warning
    assert seen["args"][seen["args"].index("--exit") + 1] == "warning"


def test_severities_ordered_high_to_low():
    assert SEVERITIES[0] == "error"
    assert SEVERITIES.index("warning") < SEVERITIES.index("info")


def test_reports_version_identity_duration_and_stderr(tmp_path):
    def fake(args, **kw):
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="pkgcheck 0.test\n", stderr="")
        return subprocess.CompletedProcess(
            args, 0, stdout="", stderr="advisory output\n")

    res = run_pkgcheck(tmp_path, runner=fake)

    assert res["complete"] is True
    assert res["state"] == "passed"
    assert res["tool_version"] == "pkgcheck 0.test"
    assert res["command"][-1] == str(tmp_path)
    assert res["input"]["path"] == str(tmp_path.resolve())
    assert res["duration_seconds"] is not None
    assert res["stderr"] == "advisory output\n"


def test_malformed_jsonstream_is_evidence_and_makes_scan_incomplete(tmp_path):
    def fake(args, **kw):
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="pkgcheck 0.test\n", stderr="")
        stdout = ('{"__class__":"ValidFinding"}\n'
                  'not-json\n'
                  '{"message":"missing class"}\n')
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    res = run_pkgcheck(tmp_path, runner=fake)

    assert res["ok"] is False
    assert res["complete"] is False
    assert [finding["code"] for finding in res["results"]] == ["ValidFinding"]
    assert len(res["malformed_output"]) == 2
    assert res["malformed_output"][0]["line"] == 2
    assert res["errors"][0]["type"] == "MalformedJsonStream"


def test_timeout_and_output_limit_are_incomplete_evidence(tmp_path):
    calls = 0

    def timeout(args, **kw):
        nonlocal calls
        calls += 1
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="pkgcheck 0.test\n", stderr="")
        raise subprocess.TimeoutExpired(
            args, kw["timeout"], output="partial", stderr="still running")

    timed_out = run_pkgcheck(tmp_path, runner=timeout, timeout=1)
    assert calls == 2
    assert timed_out["complete"] is False
    assert timed_out["truncated"] is True
    assert timed_out["timed_out"] is True
    assert timed_out["execution"]["stdout"] == "partial"

    def oversized(args, **kw):
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="pkgcheck 0.test\n", stderr="")
        return subprocess.CompletedProcess(
            args, 0, stdout="x" * 300, stderr="y" * 300)

    limited = run_pkgcheck(
        tmp_path, runner=oversized, max_output_bytes=256)
    assert limited["complete"] is False
    assert limited["truncated"] is True
    assert (limited["execution"]["stdout_bytes"]
            + limited["execution"]["stderr_bytes"]) == 256


def test_command_evidence_preserves_complete_nonzero_result(tmp_path):
    def fake(args, **kw):
        return subprocess.CompletedProcess(
            args, 2, stdout="diagnostic\n", stderr="failure\n")

    evidence = run_evidence_command(
        ["tool", "check"], cwd=tmp_path, runner=fake)

    assert evidence["complete"] is True
    assert evidence["state"] == "complete"
    assert evidence["truncated"] is False
    assert evidence["returncode"] == 2
    assert evidence["stdout"] == "diagnostic\n"
    assert evidence["stderr"] == "failure\n"


def test_command_evidence_passes_environment_to_bounded_process():
    environment = {**os.environ, "GZH_EVIDENCE_TEST": "explicit-value"}
    evidence = run_evidence_command(
        [sys.executable, "-c",
         "import os; print(os.environ['GZH_EVIDENCE_TEST'])"],
        env=environment,
    )

    assert evidence["complete"] is True
    assert evidence["returncode"] == 0
    assert evidence["stdout"] == "explicit-value\n"
