import subprocess

import pytest

import scripts.update as update
from scripts.update import find_canonical_remote, github_slug, update_checkout


def test_github_slug_accepts_supported_canonical_forms():
    assert github_slug("git@github.com:gentoo-zh/skills.git") == "gentoo-zh/skills"
    assert github_slug("https://github.com/gentoo-zh/skills.git") == "gentoo-zh/skills"
    assert github_slug("ssh://git@github.com/gentoo-zh/skills.git") == \
        "gentoo-zh/skills"


def test_github_slug_rejects_other_hosts():
    assert github_slug("https://example.com/gentoo-zh/skills.git") is None
    assert github_slug("git@example.com:gentoo-zh/skills.git") is None


def test_find_canonical_remote_uses_url_not_name(monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["git", "remote"]:
            stdout = "origin\nsource\n"
        elif args[-1] == "origin":
            stdout = "git@github.com:someone/skills.git\n"
        else:
            stdout = "https://github.com/gentoo-zh/skills.git\n"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert find_canonical_remote() == "source"


def test_find_canonical_remote_rejects_ambiguity(monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["git", "remote"]:
            stdout = "origin\nupstream\n"
        else:
            stdout = "https://github.com/gentoo-zh/skills.git\n"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="found origin, upstream"):
        find_canonical_remote()


def test_update_checkout_fetches_and_fast_forwards_canonical(monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(args, 0, "master\n", "")
        if args == ["git", "remote"]:
            return subprocess.CompletedProcess(args, 0, "upstream\n", "")
        if args[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(
                args, 0, "git@github.com:gentoo-zh/skills.git\n", "")
        if args[:4] == ["git", "rev-list", "--left-right", "--count"]:
            return subprocess.CompletedProcess(args, 0, "0\t0\n", "")
        raise AssertionError(args)

    commands = []

    def fake_command(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    monkeypatch.setattr(update, "run", fake_command)
    update_checkout()
    assert commands == [
        ["git", "fetch", "upstream", "master"],
        ["git", "merge", "--ff-only", "upstream/master"],
    ]


def test_update_checkout_rejects_topic_branch_before_fetch(monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(args, 0, "topic\n", "")
        raise AssertionError(args)

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="only master"):
        update_checkout()


def test_update_checkout_rejects_local_master_commits(monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["git", "status", "--porcelain"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:4] == ["git", "symbolic-ref", "--quiet", "--short"]:
            return subprocess.CompletedProcess(args, 0, "master\n", "")
        if args == ["git", "remote"]:
            return subprocess.CompletedProcess(args, 0, "origin\n", "")
        if args[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(
                args, 0, "git@github.com:gentoo-zh/skills.git\n", "")
        if args[:4] == ["git", "rev-list", "--left-right", "--count"]:
            return subprocess.CompletedProcess(args, 0, "1\t0\n", "")
        raise AssertionError(args)

    commands = []

    def fake_command(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    monkeypatch.setattr(update, "run", fake_command)
    with pytest.raises(RuntimeError, match="absent from origin/master"):
        update_checkout()
    assert commands == [["git", "fetch", "origin", "master"]]


def test_reference_audit_fails_on_source_drift(monkeypatch):
    commands = []

    def fake_command(args, **kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(update, "run", fake_command)
    assert update.audit_references() == 0
    assert "--fail-on-drift" in commands[0]


def test_find_canonical_remote_ignores_secondary_push_url(monkeypatch):
    def fake_run(args, **kwargs):
        if args == ["git", "remote"]:
            stdout = "origin\nupstream\n"
        elif args[-1] == "origin":
            stdout = "https://example.com/not-canonical.git\n"
        else:
            stdout = "https://github.com/gentoo-zh/skills.git\n"
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert find_canonical_remote() == "upstream"
