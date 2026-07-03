import subprocess
from pathlib import Path

from gzh.commit import run_commit


def test_commit_with_explicit_message(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        seen["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
                     message="dev-python/foo: add 1.0.0", runner=fake_run)
    assert res["ok"] is True
    assert seen["args"][:2] == ["pkgdev", "commit"]
    assert "--message" in seen["args"]
    assert "dev-python/foo: add 1.0.0" in seen["args"]
    assert seen["cwd"] == tmp_path


def test_commit_without_message(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        assert "--message" not in args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    res = run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
                     runner=fake_run)
    assert res["ok"] is True
