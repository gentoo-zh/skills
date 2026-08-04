import hashlib
import subprocess
from pathlib import Path

from gzh.buildtest import run_build_test


def _eb(tmp_path):
    eb = tmp_path / "app-misc" / "foo" / "foo-1.0.0.ebuild"
    eb.parent.mkdir(parents=True)
    eb.write_text("EAPI=8\n")
    return eb


def _portage_evidence(args):
    if args == ["portageq", "envvar", "ARCH"]:
        return subprocess.CompletedProcess(args, 0, stdout="amd64\n", stderr="")
    if args == ["eselect", "--brief", "profile", "show"]:
        return subprocess.CompletedProcess(
            args, 0, stdout="default/linux/amd64/23.0\n", stderr="")
    return None


def test_none_level_skips(tmp_path):
    res = run_build_test(_eb(tmp_path), level="none")
    assert res["ok"] is True
    assert res["skipped"] is True


def test_quick_runs_expected_phases(monkeypatch, tmp_path):
    seen = []

    def fake_run(args, **kw):
        if evidence := _portage_evidence(args):
            return evidence
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(_eb(tmp_path), level="quick", runner=fake_run)
    assert res["ok"] is True
    phases = [a[-1] for a in seen]  # last arg of each call is the phase
    assert phases == ["clean", "unpack", "prepare", "configure"]


def test_full_includes_compile_install(monkeypatch, tmp_path):
    seen = []

    def fake_run(args, **kw):
        if evidence := _portage_evidence(args):
            return evidence
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_build_test(_eb(tmp_path), level="full", runner=fake_run)
    phases = [a[-1] for a in seen]
    assert "compile" in phases and "install" in phases


def test_default_level_is_full(monkeypatch, tmp_path):
    seen = []

    def fake_run(args, **kw):
        if evidence := _portage_evidence(args):
            return evidence
        seen.append(args[-1])
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_build_test(_eb(tmp_path), runner=fake_run)  # no level → default
    assert "install" in seen  # default is full (includes install)


def test_failure_locates_phase(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        if evidence := _portage_evidence(args):
            return evidence
        # clean & unpack pass; prepare fails
        phase = args[-1]
        if phase == "prepare":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="patch fails")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(_eb(tmp_path), level="quick", runner=fake_run)
    assert res["ok"] is False
    assert res["failed_phase"] == "prepare"
    assert res["failure_reason"] == "phase_failed"
    assert "patch fails" in res["stderr"]


def test_saved_elog_fails_closed_after_successful_phases(tmp_path):
    logdir = tmp_path / "build-evidence"
    saved_message = (
        "QA: install\nQA Notice: unexpected installed path\n"
        "detail retained for review\n")

    def fake_run(args, **kwargs):
        assert kwargs["env"]["PORTAGE_ELOG_CLASSES"] == "qa warn error"
        assert kwargs["env"]["PORTAGE_ELOG_SYSTEM"] == "save"
        assert kwargs["env"]["PORTAGE_LOGDIR"] == str(logdir.resolve())
        if evidence := _portage_evidence(args):
            return evidence
        if args[-1] == "install":
            elog = logdir / "elog" / "app-misc:foo-1.0.0:20260804-120000.log"
            elog.write_text(saved_message)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(
        _eb(tmp_path), level="full", runner=fake_run, logdir=logdir)

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["failed_phase"] is None
    assert res["failure_reason"] == "elog_gate_failed"
    assert res["returncode"] == 0
    assert res["log_path"] == str(logdir.resolve())
    assert Path(res["elog_records"][0]["path"]).is_file()
    assert res["elog_records"][0] == {
        "class": "qa",
        "atom": "=app-misc/foo-1.0.0",
        "path": str(
            logdir / "elog" / "app-misc:foo-1.0.0:20260804-120000.log"),
        "phase": "install",
        "message": (
            "QA Notice: unexpected installed path\n"
            "detail retained for review"),
        "size": len(saved_message.encode()),
        "sha256": res["elog_inventory"]["entries"][0]["sha256"],
        "truncated": False,
    }


def test_build_records_bounded_identity_environment_and_commands(tmp_path):
    ebuild = _eb(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "fixture"], check=True)
    revision = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True).stdout.strip()

    def fake_run(args, **kwargs):
        if evidence := _portage_evidence(args):
            return evidence
        return subprocess.CompletedProcess(args, 0, stdout="phase output\n", stderr="")

    result = run_build_test(
        ebuild, level="quick", runner=fake_run,
        environment={"FEATURES": "test"})

    assert result["ok"] is True
    assert result["complete"] is True
    assert result["input"]["sha256"] == hashlib.sha256(ebuild.read_bytes()).hexdigest()
    assert result["input"]["git_revision"] == revision
    assert result["input"]["state"] == "git-revision-clean"
    assert result["environment"]["arch"]["value"] == "amd64"
    assert result["environment"]["profile"]["value"] == "default/linux/amd64/23.0"
    assert result["environment"]["selected"]["FEATURES"] == "test"
    assert result["commands"][:2] == [
        ["portageq", "envvar", "ARCH"],
        ["eselect", "--brief", "profile", "show"],
    ]
    assert result["commands"][2][-1] == "clean"


def test_build_fails_closed_on_bounded_phase_output(tmp_path):
    def fake_run(args, **kwargs):
        if evidence := _portage_evidence(args):
            return evidence
        return subprocess.CompletedProcess(args, 0, stdout="x" * 257, stderr="")

    result = run_build_test(
        _eb(tmp_path), level="quick", runner=fake_run, max_output_bytes=256)

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["truncated"] is True
    assert result["failure_reason"] == "phase_evidence_incomplete"
    assert result["steps"][0]["stdout_bytes"] == 256


def test_build_omits_and_hashes_oversized_selected_environment(tmp_path):
    def fake_run(args, **kwargs):
        if evidence := _portage_evidence(args):
            return evidence
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    result = run_build_test(
        _eb(tmp_path), level="quick", runner=fake_run,
        environment={"FEATURES": "x" * (16 * 1024 + 1)})

    assert result["ok"] is False
    assert result["complete"] is False
    assert result["failure_reason"] == "environment_evidence_incomplete"
    assert result["environment"]["selected"]["FEATURES"] == {
        "bytes": 16 * 1024 + 1,
        "omitted": True,
        "sha256": hashlib.sha256(b"x" * (16 * 1024 + 1)).hexdigest(),
    }


def test_build_stops_before_phases_when_environment_evidence_is_incomplete(
        tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args == ["portageq", "envvar", "ARCH"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed")
        if args == ["eselect", "--brief", "profile", "show"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="default/linux/amd64/23.0\n", stderr="")
        raise AssertionError(f"build phase ran after failed preflight: {args}")

    result = run_build_test(_eb(tmp_path), level="full", runner=fake_run)

    assert result["ok"] is False
    assert result["failure_reason"] == "environment_evidence_incomplete"
    assert result["steps"] == []
    assert calls == [
        ["portageq", "envvar", "ARCH"],
        ["eselect", "--brief", "profile", "show"],
    ]
