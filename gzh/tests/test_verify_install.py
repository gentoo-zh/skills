import subprocess

from gzh.verify_install import atom_from_ebuild, run_verify_install


def _ebuild(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "repo_name").write_text("gentoo-zh\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = tmp_path / "app-misc" / "foo" / "foo-1.2.3.ebuild"
    path.parent.mkdir(parents=True)
    path.write_text("EAPI=8\n")
    return path


def test_atom_from_ebuild(tmp_path):
    assert atom_from_ebuild(_ebuild(tmp_path)) == \
        "=app-misc/foo-1.2.3::gentoo-zh"


def test_verify_install_matches_ci_order_and_environment(tmp_path):
    ebuild = _ebuild(tmp_path)
    logdir = tmp_path / "logs"
    seen = []

    def fake_run(args, **kwargs):
        seen.append((args, kwargs["env"]))
        if "--onlydeps" in args:
            elog = logdir / "elog"
            elog.mkdir(parents=True, exist_ok=True)
            (elog / "dependency.log").write_text("ignored dependency warning\n")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    res = run_verify_install(ebuild, logdir=logdir, runner=fake_run)
    assert res["ok"] is True
    assert [call[0] for call in seen] == [
        ["emerge", "--usepkg=n", "--usepkgonly=n", "--onlydeps",
         "=app-misc/foo-1.2.3::gentoo-zh"],
        ["emerge", "--usepkg=n", "--usepkgonly=n", "--oneshot",
         "--selective=n", "=app-misc/foo-1.2.3::gentoo-zh"],
    ]
    assert seen[0][1]["PORTAGE_ELOG_CLASSES"] == "qa warn error"
    assert seen[0][1]["PORTAGE_ELOG_SYSTEM"] == "save"
    assert seen[0][1]["PORTAGE_LOGDIR"] == str(logdir.resolve())
    assert not (logdir / "elog" / "dependency.log").exists()


def test_verify_install_fails_on_saved_elog(tmp_path):
    ebuild = _ebuild(tmp_path)
    logdir = tmp_path / "logs"

    def fake_run(args, **kwargs):
        if "--onlydeps" not in args:
            elog = logdir / "elog"
            elog.mkdir(parents=True, exist_ok=True)
            (elog / "app-misc:foo-1.2.3:1.log").write_text("QA Notice: failure\n")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_verify_install(ebuild, logdir=logdir, runner=fake_run)
    assert res["ok"] is False
    assert res["failed_step"] == "elog"
    assert "QA Notice" in res["elog_files"][0]["text"]


def test_verify_install_stops_when_onlydeps_fails(tmp_path):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="deps failed")

    res = run_verify_install(_ebuild(tmp_path), logdir=tmp_path / "logs",
                             runner=fake_run)
    assert res["failed_step"] == "onlydeps"
    assert len(calls) == 1


def test_atom_rejects_other_repository(tmp_path):
    ebuild = _ebuild(tmp_path)
    (tmp_path / "profiles" / "repo_name").write_text("other\n")
    try:
        atom_from_ebuild(ebuild)
    except ValueError as exc:
        assert "gentoo-zh development checkout" in str(exc)
    else:
        raise AssertionError("expected another repository to be rejected")
