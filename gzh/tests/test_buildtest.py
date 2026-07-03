import subprocess
from pathlib import Path

from gzh.buildtest import run_build_test


def _eb(tmp_path):
    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    return eb


def test_none_level_skips(tmp_path):
    res = run_build_test(_eb(tmp_path), level="none")
    assert res["ok"] is True
    assert res["skipped"] is True


def test_quick_runs_expected_phases(monkeypatch, tmp_path):
    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(_eb(tmp_path), level="quick", runner=fake_run)
    assert res["ok"] is True
    phases = [a[-1] for a in seen]  # last arg of each call is the phase
    assert phases == ["clean", "unpack", "prepare", "configure"]


def test_full_includes_compile_install(monkeypatch, tmp_path):
    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_build_test(_eb(tmp_path), level="full", runner=fake_run)
    phases = [a[-1] for a in seen]
    assert "compile" in phases and "install" in phases


def test_failure_locates_phase(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        # clean & unpack pass; prepare fails
        phase = args[-1]
        if phase == "prepare":
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="patch fails")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(_eb(tmp_path), level="quick", runner=fake_run)
    assert res["ok"] is False
    assert res["failed_phase"] == "prepare"
    assert "patch fails" in res["stderr"]
