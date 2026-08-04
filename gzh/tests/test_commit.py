import subprocess
from pathlib import Path

from gzh.commit import run_commit, run_recommit


def test_commit_with_explicit_message(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kw):
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="old-head\n", stderr="")
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
    seen = {}

    def fake_run(args, **kw):
        assert "--message" not in args
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="old-head\n", stderr="")
        if args[:2] == ["pkgdev", "commit"]:
            seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    res = run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
                     runner=fake_run)
    assert res["ok"] is True
    assert "--signoff=true" in seen["args"]
    assert "--signoff" not in seen["args"]


def test_commit_disables_pkgdev_scan(tmp_path):
    """gzh qa is the hard gate, so pkgdev must not scan a second time."""
    seen = {}

    def fake_run(args, **kw):
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="old-head\n", stderr="")
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path, runner=fake_run)
    args = seen["args"]
    assert args[args.index("--scan") + 1] == "false"


def test_commit_gpg_signs_when_a_key_is_configured(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="old-head\n", stderr="")
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 0, stdout="ABCD1234\n", stderr="")
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path, runner=fake_run)
    assert "--gpg-sign" in seen["args"]


def test_commit_omits_gpg_sign_without_a_key(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="old-head\n", stderr="")
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path, runner=fake_run)
    assert "--gpg-sign" not in seen["args"]


def test_commit_does_not_add_sign_flag_for_commit_gpgsign(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="old-head\n", stderr="")
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.ebuild"], cwd=tmp_path, runner=fake_run)
    assert "--gpg-sign" not in seen["args"]


def test_commit_stops_when_git_add_fails(tmp_path):
    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        if args == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:2] == ["git", "add"]:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="bad path")
        if args[:4] == ["git", "reset", "--mixed", "HEAD"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        raise AssertionError(f"commit must not run after staging failed: {args}")

    res = run_commit([tmp_path / "missing.ebuild"], cwd=tmp_path, runner=fake_run)
    assert res["ok"] is False
    assert res["stage"] == "git-add"
    assert res["index_restored"] is True
    assert not any(args[:2] == ["pkgdev", "commit"] for args in seen)


def test_commit_uses_existing_package_scope_for_deleted_ebuild(tmp_path):
    package = tmp_path / "cat" / "pkg"
    package.mkdir(parents=True)
    new = package / "pkg-2.ebuild"
    new.write_text("EAPI=8\n")
    old = package / "pkg-1.ebuild"
    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        if args == ["git", "diff", "--cached", "--name-status",
                    "--find-renames", "--", "cat/pkg"]:
            return subprocess.CompletedProcess(
                args, 0,
                stdout="R100\tcat/pkg/pkg-1.ebuild\tcat/pkg/pkg-2.ebuild\n",
                stderr="")
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="old-head\n", stderr="")
        if args[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="cat/pkg: add 2, drop 1\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    result = run_commit([new, old], cwd=tmp_path, runner=fake_run)
    assert result["ok"] is True
    assert result["pathspecs"] == ["cat/pkg"]
    pkgdev = next(args for args in seen if args[:2] == ["pkgdev", "commit"])
    assert pkgdev[-1] == "cat/pkg"
    git_add = next(args for args in seen if args[:2] == ["git", "add"])
    assert git_add[-2:] == ["cat/pkg/pkg-2.ebuild", "cat/pkg/pkg-1.ebuild"]


def test_commit_rejects_generated_subject_that_omits_drop(tmp_path):
    package = tmp_path / "cat" / "pkg"
    package.mkdir(parents=True)
    new = package / "pkg-2.ebuild"
    new.write_text("EAPI=8\n")
    old = package / "pkg-1.ebuild"

    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        if args[:5] == ["git", "diff", "--cached", "--name-status",
                        "--find-renames"]:
            return subprocess.CompletedProcess(
                args, 0,
                stdout="R100\tcat/pkg/pkg-1.ebuild\tcat/pkg/pkg-2.ebuild\n",
                stderr="")
        if args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        if args == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="old-head\n", stderr="")
        if args[:2] == ["git", "log"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="cat/pkg: add 2\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    result = run_commit([new, old], cwd=tmp_path, runner=fake_run)
    assert result["ok"] is False
    assert result["stage"] == "subject-validation"
    assert result["commit_created"] is False
    assert result["rolled_back"] is True
    assert result["expected_subject"] == "cat/pkg: add 2, drop 1"
    assert ["git", "reset", "--mixed", "old-head"] in seen


def test_commit_rejects_wrong_explicit_bump_subject_before_pkgdev(tmp_path):
    package = tmp_path / "cat" / "pkg"
    package.mkdir(parents=True)
    new = package / "pkg-2.ebuild"
    new.write_text("EAPI=8\n")
    seen = []

    def fake_run(args, **kw):
        seen.append(args)
        if args[:5] == ["git", "diff", "--cached", "--name-status",
                        "--find-renames"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="A\tcat/pkg/pkg-2.ebuild\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    result = run_commit(
        [new], cwd=tmp_path, message="cat/pkg: add 3", runner=fake_run)
    assert result["ok"] is False
    assert result["stage"] == "message-validation"
    assert result["index_restored"] is True
    assert ["git", "reset", "--mixed", "HEAD", "--", "cat/pkg/pkg-2.ebuild"] in seen
    assert not any(args[:2] == ["pkgdev", "commit"] for args in seen)


def test_commit_rejects_preexisting_staged_changes(tmp_path):
    def fake_run(args, **kw):
        if args == ["git", "diff", "--cached", "--quiet"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        raise AssertionError(f"must stop before staging: {args}")

    result = run_commit([tmp_path / "cat/pkg/pkg-1.ebuild"], cwd=tmp_path,
                        runner=fake_run)
    assert result["ok"] is False
    assert result["stage"] == "preflight"


def _init_commit_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root,
                   check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root,
                   check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-qm", "base"], cwd=root,
                   check=True)


def test_failed_pkgdev_preserves_worktree_and_unstages_owned_paths(tmp_path):
    _init_commit_repo(tmp_path)
    ebuild = tmp_path / "cat" / "pkg" / "pkg-1.ebuild"
    ebuild.parent.mkdir(parents=True)
    ebuild.write_text("EAPI=8\n", encoding="utf-8")

    def runner(args, **kwargs):
        if args[:2] == ["pkgdev", "commit"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed")
        return subprocess.run(args, **kwargs)

    result = run_commit([ebuild], cwd=tmp_path, runner=runner)
    assert result["ok"] is False
    assert result["index_restored"] is True
    assert ebuild.read_text(encoding="utf-8") == "EAPI=8\n"
    cached = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path,
        check=True, capture_output=True, text=True)
    assert cached.stdout == ""


def test_failed_commit_restores_only_owned_paths_when_index_is_shared(tmp_path):
    _init_commit_repo(tmp_path)
    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "add notes"], cwd=tmp_path,
                   check=True)
    unrelated.write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "add", "notes.txt"], cwd=tmp_path, check=True)
    ebuild = tmp_path / "cat" / "pkg" / "pkg-1.ebuild"
    ebuild.parent.mkdir(parents=True)
    ebuild.write_text("EAPI=8\n", encoding="utf-8")

    def runner(args, **kwargs):
        if args[:2] == ["pkgdev", "commit"]:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="failed")
        return subprocess.run(args, **kwargs)

    result = run_commit(
        [ebuild], cwd=tmp_path, runner=runner, require_clean_index=False)
    assert result["ok"] is False
    cached = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=tmp_path,
        check=True, capture_output=True, text=True)
    assert cached.stdout.splitlines() == ["notes.txt"]


def test_commit_stages_symlink_itself_without_dereferencing(tmp_path):
    _init_commit_repo(tmp_path)
    target = tmp_path / "target.txt"
    target.write_text("target\n", encoding="utf-8")
    subprocess.run(["git", "add", "target.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "target"], cwd=tmp_path, check=True)
    link = tmp_path / "link.txt"
    link.symlink_to("target.txt")

    def runner(args, **kwargs):
        if args[:2] == ["pkgdev", "commit"]:
            return subprocess.run(
                ["git", "commit", "-qm", "add link"], cwd=kwargs["cwd"],
                capture_output=True, text=True)
        return subprocess.run(args, **kwargs)

    result = run_commit([link], cwd=tmp_path, runner=runner)
    changed = subprocess.run(
        ["git", "show", "--format=", "--name-only", "HEAD"], cwd=tmp_path,
        check=True, capture_output=True, text=True).stdout.splitlines()

    assert result["ok"] is True
    assert changed == ["link.txt"]


def test_commit_rejects_path_below_symlink_directory(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    result = run_commit([linked / "file"], cwd=tmp_path, runner=subprocess.run)

    assert result["ok"] is False
    assert result["stage"] == "paths"
    assert "traverses a symlink" in result["error"]


def _recommit_runner(
        seen, *, commit_count="1", pkgdev_code=0,
        committed_paths=("cat/pkg/pkg-1.ebuild",), staged_after=False):
    cached_checks = 0

    def fake_run(args, **kwargs):
        nonlocal cached_checks
        seen.append(args)
        if args == ["git", "diff", "--cached", "--quiet"]:
            cached_checks += 1
            code = 1 if staged_after and cached_checks > 1 else 0
            return subprocess.CompletedProcess(args, code, "", "")
        if args[:3] == ["git", "remote", "-v"]:
            output = "upstream\tgit@github.com:gentoo-zh/overlay.git (fetch)\n"
        elif args[:2] == ["git", "merge-base"]:
            output = "base\n"
        elif args[:3] == ["git", "rev-list", "--count"]:
            output = f"{commit_count}\n"
        elif args == ["git", "rev-parse", "HEAD"]:
            output = "old-head\n"
        elif args == ["git", "rev-parse", "HEAD^"]:
            output = "parent\n"
        elif args[:3] == ["git", "diff-tree", "--no-commit-id"]:
            output = "\0".join(committed_paths) + "\0"
        elif args[:3] == ["git", "log", "-1"]:
            output = "cat/pkg: add 1\n"
        elif args[:3] == ["git", "config", "--get"]:
            return subprocess.CompletedProcess(args, 1, "", "")
        elif args[:2] == ["pkgdev", "commit"]:
            return subprocess.CompletedProcess(args, pkgdev_code, "", "failed")
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, output, "")
    return fake_run


def test_recommit_rebuilds_single_local_commit_with_pkgdev(tmp_path):
    seen = []
    result = run_recommit(
        [tmp_path / "cat/pkg/pkg-1.ebuild"], cwd=tmp_path,
        runner=_recommit_runner(seen))
    assert result["ok"] is True
    assert ["git", "reset", "--soft", "parent"] in seen
    assert any(args[:2] == ["pkgdev", "commit"] for args in seen)
    assert not any(args[:2] == ["git", "commit"] for args in seen)


def test_recommit_rejects_multiple_local_commits(tmp_path):
    seen = []
    result = run_recommit(
        [tmp_path / "cat/pkg/pkg-1.ebuild"], cwd=tmp_path,
        runner=_recommit_runner(seen, commit_count="2"))
    assert result["ok"] is False
    assert result["stage"] == "commit-count"
    assert not any(args[:2] == ["git", "reset"] for args in seen)


def test_recommit_rolls_back_when_pkgdev_fails(tmp_path):
    seen = []
    result = run_recommit(
        [tmp_path / "cat/pkg/pkg-1.ebuild"], cwd=tmp_path,
        runner=_recommit_runner(seen, pkgdev_code=1))
    assert result["ok"] is False
    assert result["rolled_back"] is True
    assert ["git", "reset", "--mixed", "old-head"] in seen


def test_recommit_rejects_paths_that_omit_part_of_existing_commit(tmp_path):
    seen = []
    result = run_recommit(
        [tmp_path / "cat/pkg/pkg-1.ebuild"], cwd=tmp_path,
        runner=_recommit_runner(
            seen,
            committed_paths=("cat/pkg/pkg-1.ebuild", "cat/pkg/Manifest")))

    assert result["ok"] is False
    assert result["stage"] == "path-coverage"
    assert result["missing_paths"] == ["cat/pkg/Manifest"]
    assert not any(args[:2] == ["git", "reset"] for args in seen)


def test_recommit_rolls_back_when_replacement_leaves_staged_paths(tmp_path):
    seen = []
    result = run_recommit(
        [tmp_path / "cat/pkg/pkg-1.ebuild"], cwd=tmp_path,
        runner=_recommit_runner(seen, staged_after=True))

    assert result["ok"] is False
    assert result["stage"] == "recommit-validation"
    assert result["rolled_back"] is True
    assert ["git", "reset", "--mixed", "old-head"] in seen
