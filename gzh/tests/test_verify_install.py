import os
import subprocess
from pathlib import Path

import pytest

import gzh.verify_install as verify_install
from gzh.verify_install import atom_from_ebuild, run_verify_install


ATOM = "=app-misc/foo-1.2.3::gentoo-zh"
PROFILE = "default/linux/amd64/23.0/desktop/systemd"


def _ebuild(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "repo_name").write_text("gentoo-zh\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = tmp_path / "app-misc" / "foo" / "foo-1.2.3.ebuild"
    path.parent.mkdir(parents=True)
    path.write_text("EAPI=8\n")
    return path


def _runner(logdir, seen, *, fail=None):
    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        if args == ["emerge", "--version"]:
            if fail == "tool":
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="missing")
            return subprocess.CompletedProcess(
                args, 0, stdout="Portage 3.0.test\n", stderr="")
        if args == ["eselect", "--brief", "profile", "show"]:
            if fail == "profile":
                stdout = ""
            elif fail == "profile-multiline":
                stdout = f"Current profile:\n{PROFILE}\n"
            else:
                stdout = f"{PROFILE}\n"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if args == ["portageq", "envvar", "ARCH"]:
            stdout = "invalid arch\n" if fail == "arch" else "amd64\n"
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")
        if "--onlydeps" in args:
            if fail == "onlydeps-timeout":
                raise subprocess.TimeoutExpired(
                    args, kwargs["timeout"], output="partial", stderr="running")
            if fail == "dependency-elog":
                elog = logdir / "elog"
                elog.mkdir(parents=True, exist_ok=True)
                (elog / "dependency.log").write_text("dependency warning\n")
            return subprocess.CompletedProcess(args, 0, stdout="deps ok", stderr="")
        if fail == "merge-output":
            return subprocess.CompletedProcess(
                args, 0, stdout="x" * 300, stderr="y" * 300)
        if fail == "merge":
            return subprocess.CompletedProcess(
                args, 1, stdout="", stderr="merge failed")
        if fail in {"elog", "elog-large"}:
            elog = logdir / "elog"
            elog.mkdir(parents=True, exist_ok=True)
            (elog / "app-misc:foo-1.2.3:1.log").write_text(
                ("QA Notice: failure\n" if fail == "elog" else "Q" * 300))
        return subprocess.CompletedProcess(args, 0, stdout="merge ok", stderr="")

    return fake_run


def test_atom_from_ebuild(tmp_path):
    assert atom_from_ebuild(_ebuild(tmp_path)) == ATOM


def test_verify_install_records_bounded_environment_and_merge_evidence(tmp_path):
    ebuild = _ebuild(tmp_path)
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        ebuild, logdir=logdir, runner=_runner(logdir, seen), timeout=42,
        max_output_bytes=4096)

    assert res["ok"] is True
    assert res["complete"] is True
    assert res["state"] == "passed"
    assert res["timed_out"] is False
    assert res["truncated"] is False
    assert res["tool"]["emerge"]["version"] == "Portage 3.0.test"
    assert res["environment"]["profile"]["value"] == PROFILE
    assert res["environment"]["arch"]["value"] == "amd64"
    assert [step["name"] for step in res["steps"]] == ["onlydeps", "merge"]
    assert [step["command"] for step in res["steps"]] == [
        ["emerge", "--usepkg=n", "--onlydeps", ATOM],
        ["emerge", "--usepkg=n", "--oneshot",
         "--selective=n", ATOM],
    ]
    assert res["commands"] == [call[0] for call in seen]
    assert res["elog"]["path"] == str((logdir / "elog").resolve())
    assert res["elog"]["entries"] == []
    assert res["elog"]["complete"] is True

    emerge_calls = [call for call in seen if call[0][0] == "emerge"
                    and call[0] != ["emerge", "--version"]]
    assert all(call[1]["timeout"] == 42 for call in emerge_calls)
    assert emerge_calls[0][1]["env"]["PORTAGE_ELOG_CLASSES"] == "qa warn error"
    assert emerge_calls[0][1]["env"]["PORTAGE_ELOG_SYSTEM"] == "save"
    assert emerge_calls[0][1]["env"]["PORTAGE_LOGDIR"] == str(logdir.resolve())


@pytest.mark.parametrize(
    "failure", ["tool", "profile", "profile-multiline", "arch"])
def test_verify_install_stops_on_incomplete_tool_or_environment_evidence(
        tmp_path, failure):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail=failure))

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["state"] == "environment-incomplete"
    assert res["failed_step"] == "preflight"
    assert len(seen) == 3
    assert all("--onlydeps" not in args for args, _kwargs in seen)


def test_verify_install_rejects_a_nonempty_explicit_elog_directory(tmp_path):
    logdir = tmp_path / "logs"
    elog = logdir / "elog"
    elog.mkdir(parents=True)
    (elog / "stale.log").write_text("old evidence\n")
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir, runner=_runner(logdir, seen))

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["state"] == "preflight-failed"
    assert res["failed_step"] == "preflight"
    assert res["initial_elog"]["entries"][0]["path"] == str(
        elog / "stale.log")
    assert len(seen) == 3


def test_verify_install_rejects_a_symlinked_elog_directory(tmp_path):
    logdir = tmp_path / "logs"
    outside = tmp_path / "outside"
    outside.mkdir()
    logdir.mkdir()
    (logdir / "elog").symlink_to(outside, target_is_directory=True)
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir, runner=_runner(logdir, seen))

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["state"] == "preflight-failed"
    assert res["failed_step"] == "preflight"
    assert res["initial_elog"]["exists"] is True
    assert "not a directory" in res["initial_elog"]["errors"][0]
    assert len(seen) == 3


def test_verify_install_rejects_a_symlinked_log_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    logdir = tmp_path / "logs"
    logdir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        run_verify_install(
            _ebuild(tmp_path), logdir=logdir,
            runner=_runner(outside, []))


def test_verify_install_rejects_elog_directory_replacement(tmp_path, monkeypatch):
    logdir = tmp_path / "logs"
    elog = logdir / "elog"
    displaced = logdir / "elog-displaced"
    seen = []
    real_open = os.open
    replaced = False

    def replacing_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if Path(path) == elog and not replaced:
            elog.rename(displaced)
            elog.mkdir()
            replaced = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(verify_install.os, "open", replacing_open)

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir, runner=_runner(logdir, seen))

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["state"] == "preflight-failed"
    assert res["failed_step"] == "preflight"
    assert "changed before it was opened" in res["initial_elog"]["errors"][0]
    assert len(seen) == 3


def test_verify_install_rejects_an_elog_created_during_inventory(
        tmp_path, monkeypatch):
    logdir = tmp_path / "logs"
    elog = logdir / "elog"
    seen = []
    real_listdir = os.listdir
    listed = False

    def changing_listdir(path):
        nonlocal listed
        names = real_listdir(path)
        if not listed:
            (elog / "late.log").write_text("late QA warning\n")
            listed = True
        return names

    monkeypatch.setattr(verify_install.os, "listdir", changing_listdir)

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir, runner=_runner(logdir, seen))

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["state"] == "preflight-failed"
    assert res["failed_step"] == "preflight"
    assert res["initial_elog"]["entries"] == []
    assert "changed during inventory" in res["initial_elog"]["errors"][0]
    assert (elog / "late.log").is_file()
    assert len(seen) == 3


def test_verify_install_timeout_is_incomplete_and_stops_before_merge(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail="onlydeps-timeout"), timeout=1)

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["timed_out"] is True
    assert res["truncated"] is True
    assert res["state"] == "timed-out"
    assert res["failed_step"] == "onlydeps"
    assert [step["name"] for step in res["steps"]] == ["onlydeps"]
    assert len(seen) == 4


def test_verify_install_truncated_merge_evidence_fails_closed(tmp_path):
    logdir = tmp_path / "logs"

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, [], fail="merge-output"),
        max_output_bytes=256)

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["timed_out"] is False
    assert res["truncated"] is True
    assert res["state"] == "truncated"
    assert res["failed_step"] == "merge"
    assert res["steps"][-1]["state"] == "truncated"
    assert (res["steps"][-1]["stdout_bytes"]
            + res["steps"][-1]["stderr_bytes"]) == 256


def test_verify_install_preserves_complete_merge_failure(tmp_path):
    logdir = tmp_path / "logs"

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, [], fail="merge"))

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["state"] == "failed"
    assert res["failed_step"] == "merge"
    assert res["steps"][-1]["returncode"] == 1
    assert res["steps"][-1]["stderr"] == "merge failed"


def test_verify_install_fails_on_saved_elog_and_records_inventory(tmp_path):
    logdir = tmp_path / "logs"

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, [], fail="elog"))

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["state"] == "failed"
    assert res["failed_step"] == "elog"
    assert res["elog"]["complete"] is True
    assert res["elog"]["truncated"] is False
    assert res["elog"]["entries"][0]["kind"] == "file"
    assert res["elog"]["entries"][0]["sha256"]
    assert "QA Notice" in res["elog_files"][0]["text"]


def test_verify_install_fails_on_dependency_elog_without_clearing_it(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail="dependency-elog"))

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["state"] == "failed"
    assert res["failed_step"] == "elog"
    assert [step["name"] for step in res["steps"]] == ["onlydeps"]
    assert res["dependency_elog"]["observed"]["entries"][0]["kind"] == "file"
    assert res["dependency_elog"]["after_clear"]["state"] == "not-collected"
    assert res["elog_files"][0]["step"] == "onlydeps"
    assert (logdir / "elog" / "dependency.log").is_file()


def test_verify_install_truncated_elog_evidence_still_fails_elog_gate(tmp_path):
    logdir = tmp_path / "logs"

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, [], fail="elog-large"), max_output_bytes=256)

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["state"] == "truncated"
    assert res["failed_step"] == "elog"
    assert res["elog"]["truncated"] is True
    assert res["elog_files"][0]["truncated"] is True


def test_verify_install_stops_when_onlydeps_fails(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    def fake_run(args, **kwargs):
        if args == ["emerge", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="Portage 3.0.test\n", stderr="")
        if args == ["eselect", "--brief", "profile", "show"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"{PROFILE}\n", stderr="")
        if args == ["portageq", "envvar", "ARCH"]:
            return subprocess.CompletedProcess(args, 0, stdout="amd64\n", stderr="")
        seen.append(args)
        return subprocess.CompletedProcess(
            args, 1, stdout="", stderr="deps failed")

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir, runner=fake_run)

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["failed_step"] == "onlydeps"
    assert len(seen) == 1


def test_atom_rejects_other_repository(tmp_path):
    ebuild = _ebuild(tmp_path)
    (tmp_path / "profiles" / "repo_name").write_text("other\n")
    with pytest.raises(ValueError, match="gentoo-zh development checkout"):
        atom_from_ebuild(ebuild)
