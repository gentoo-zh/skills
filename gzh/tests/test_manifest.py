import subprocess
from pathlib import Path

from gzh.manifest import run_manifest


def test_manifest_success(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        captured["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    res = run_manifest(eb, cwd=tmp_path, runner=fake_run)
    assert res["ok"] is True
    assert captured["args"][:2] == ["pkgdev", "manifest"]
    assert "--force" in captured["args"]
    assert captured["cwd"] == tmp_path


def test_manifest_failure(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="fetch failed")
    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    res = run_manifest(eb, cwd=tmp_path, runner=fake_run)
    assert res["ok"] is False
    assert "fetch failed" in res["stderr"]
