import subprocess
from pathlib import Path

from gzh.repo import (find_canonical_remote, find_overlay_root, github_slug,
                      is_portage_synced_repo, validate_canonical_remote,
                      validate_overlay_root)


def _mark_overlay(root):
    profiles = root / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "repo_name").write_text("gentoo-zh\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


def test_env_override_wins(monkeypatch, tmp_path):
    _mark_overlay(tmp_path)
    monkeypatch.setenv("GZH_OVERLAY_DIR", str(tmp_path))
    assert find_overlay_root() == tmp_path.resolve()


def test_git_toplevel_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GZH_OVERLAY_DIR", raising=False)
    _mark_overlay(tmp_path)

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


def test_env_override_rejects_arbitrary_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("GZH_OVERLAY_DIR", str(tmp_path))
    try:
        find_overlay_root()
    except RuntimeError as exc:
        assert "not a gentoo-zh overlay" in str(exc)
    else:
        raise AssertionError("expected an invalid overlay to be rejected")


def test_repo_name_without_git_is_rejected(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "repo_name").write_text("gentoo-zh\n")
    try:
        validate_overlay_root(tmp_path)
    except RuntimeError as exc:
        assert "Git development checkout" in str(exc)
    else:
        raise AssertionError("expected a non-Git directory to be rejected")


def test_portage_synced_repository_path_is_rejected():
    assert is_portage_synced_repo(Path("/var/db/repos/gentoo-zh")) is True


def test_canonical_remote_accepts_legacy_repository(tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0,
            stdout="origin\thttps://github.com/microcai/gentoo-zh.git (fetch)\n",
            stderr="")

    assert find_canonical_remote(tmp_path, runner=fake_run) == "origin"


def test_canonical_remote_prefers_current_repository(tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0,
            stdout=("origin\tgit@github.com:microcai/gentoo-zh.git (fetch)\n"
                    "upstream\thttps://github.com/gentoo-zh/overlay.git (fetch)\n"),
            stderr="")

    assert find_canonical_remote(tmp_path, runner=fake_run) == "upstream"


def test_canonical_remote_resolves_duplicate_aliases(tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0,
            stdout=("canonical\tgit@github.com:gentoo-zh/overlay.git (fetch)\n"
                    "upstream\tgit@github.com:gentoo-zh/overlay.git (fetch)\n"),
            stderr="")

    assert find_canonical_remote(tmp_path, runner=fake_run) == "upstream"


def test_overlay_github_slug_rejects_lookalike_host():
    assert github_slug("https://evilgithub.com/gentoo-zh/overlay.git") is None
    assert github_slug("git@example.com:gentoo-zh/overlay.git") is None


def test_validate_canonical_remote_checks_primary_fetch_url(tmp_path):
    def fake_run(args, **kwargs):
        assert args == ["git", "remote", "get-url", "upstream"]
        return subprocess.CompletedProcess(
            args, 0, stdout="git@github.com:gentoo-zh/overlay.git\n", stderr="")

    assert validate_canonical_remote(tmp_path, "upstream", runner=fake_run) == "upstream"


def test_validate_canonical_remote_rejects_personal_fork(tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, 0, stdout="git@github.com:someone/overlay.git\n", stderr="")

    try:
        validate_canonical_remote(tmp_path, "origin", runner=fake_run)
    except RuntimeError as exc:
        assert "does not point" in str(exc)
    else:
        raise AssertionError("expected a personal fork remote to be rejected")
