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


def test_commit_disables_pkgdev_scan(tmp_path):
    """gzh pkgcheck is the hard gate, so pkgdev must not scan a second time."""
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path, runner=fake_run)
    args = seen["args"]
    assert args[args.index("--scan") + 1] == "false"


def test_commit_gpg_signs_when_a_key_is_configured(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 0, stdout="ABCD1234\n", stderr="")
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path, runner=fake_run)
    assert "--gpg-sign" in seen["args"]


def test_commit_omits_gpg_sign_without_a_key(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path, runner=fake_run)
    assert "--gpg-sign" not in seen["args"]
