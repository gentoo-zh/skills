import subprocess
from pathlib import Path

from gzh.repo import find_overlay_root


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("GZH_OVERLAY_DIR", str(tmp_path))
    assert find_overlay_root() == tmp_path.resolve()


def test_git_toplevel_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GZH_OVERLAY_DIR", raising=False)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{tmp_path}\n", stderr="")

    monkeypatch.setattr("gzh.repo.subprocess.run", fake_run)
    assert find_overlay_root(Path.cwd()) == tmp_path


def test_not_a_repo_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("GZH_OVERLAY_DIR", raising=False)

    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr("gzh.repo.subprocess.run", fake_run)
    try:
        find_overlay_root(tmp_path)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")
