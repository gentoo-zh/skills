import os
import subprocess
from pathlib import Path

import pytest

import gzh.verify_install as verify_install
from gzh.verify_install import (
    atom_from_ebuild,
    parse_emerge_plan,
    run_verify_install,
)


ATOM = "=app-misc/foo-1.2.3::gentoo-zh"
PROFILE = "default/linux/amd64/23.0/desktop/systemd"
REPOSITORIES = """[DEFAULT]
main-repo = gentoo

[gentoo]
location = /var/db/repos/gentoo

[gentoo-zh]
location = /var/db/repos/gentoo-zh
masters = gentoo
"""


def _plan(*actions):
    return (
        "These are the packages that would be merged, in reverse order:\n\n"
        + "\n".join(actions)
        + f"\n\nTotal: {len(actions)} package"
        + ("s" if len(actions) != 1 else "")
        + "\n")


TARGET_ACTION = "[ebuild   R    ] app-misc/foo-1.2.3::gentoo-zh"


def _ebuild(tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir(exist_ok=True)
    (profiles / "repo_name").write_text("gentoo-zh\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = tmp_path / "app-misc" / "foo" / "foo-1.2.3.ebuild"
    path.parent.mkdir(parents=True)
    path.write_text("EAPI=8\n")
    return path


def _runner(logdir, seen, *, fail=None, active_root="/"):
    system_root = logdir.parent / "system-root"
    (system_root / "etc" / "portage").mkdir(parents=True, exist_ok=True)

    def fake_run(args, **kwargs):
        seen.append((args, kwargs))
        if args == ["portageq", "envvar", "PORTAGE_REPOSITORIES"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=REPOSITORIES, stderr="")
        if args == ["portageq", "envvar", "PORTAGE_CONFIGROOT"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"{system_root}\n", stderr="")
        if args == ["portageq", "envvar", "ROOT"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"{active_root}\n", stderr="")
        if args == ["portageq", "envvar", "ACCEPT_KEYWORDS"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="amd64\n", stderr="")
        if args == [
                "portageq", "get_repo_path", str(Path(active_root).resolve()),
                "gentoo-zh"]:
            return subprocess.CompletedProcess(
                args, 0, stdout=f"{logdir.parent}\n", stderr="")
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
        if args and args[0] == "emerge" and "--pretend" in args:
            if fail == "pretend-timeout":
                raise subprocess.TimeoutExpired(
                    args, kwargs["timeout"], output="partial", stderr="running")
            if fail == "pretend":
                return subprocess.CompletedProcess(
                    args, 1, stdout="", stderr="resolution failed")
            if fail == "plan-unrelated":
                output = _plan(
                    "[ebuild   U    ] dev-libs/unrelated-2::gentoo [1::gentoo]",
                    TARGET_ACTION)
            elif fail == "plan-new-dependency":
                output = _plan(
                    "[binary   N    ] dev-libs/new-dependency-2::gentoo",
                    TARGET_ACTION)
            elif fail == "plan-malformed":
                output = "Total: 2 packages\n" + TARGET_ACTION + "\n"
            else:
                output = _plan(TARGET_ACTION)
            return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")
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
    assert res["environment"]["root"]["value"] == "/"
    assert [step["name"] for step in res["steps"]] == ["pretend", "merge"]
    assert "--pretend" in res["steps"][0]["command"]
    for step in res["steps"]:
        assert "--ignore-default-opts" in step["command"]
        assert "--autounmask=n" in step["command"]
        assert "--usepkg=y" in step["command"]
        assert "--usepkg-exclude=app-misc/foo" in step["command"]
        assert any(value.startswith("--config-root=") for value in step["command"])
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
    repositories = emerge_calls[0][1]["env"]["PORTAGE_REPOSITORIES"]
    assert f"location = {tmp_path}" in repositories
    assert "location = /var/db/repos/gentoo\n" in repositories
    assert "location = /var/db/repos/gentoo-zh" not in repositories
    assert res["repository_binding"]["worktree"] == str(tmp_path)
    assert res["repository_binding"]["sha256"]
    assert res["keyword_configuration"]["target"]["line"] == f"{ATOM} ~amd64"
    assert res["keyword_configuration"]["cleanup"]["complete"] is True
    assert res["environment"]["accept_keywords"]["baseline"] == "amd64"
    assert res["environment"]["accept_keywords"]["value"] == "amd64"
    assert res["plan"]["classified"]["target"][0]["source"] == "ebuild"
    assert res["plan"]["unauthorized"] == []


def test_verify_install_uses_the_active_root_for_repository_evidence(tmp_path):
    ebuild = _ebuild(tmp_path)
    logdir = tmp_path / "logs"
    target_root = tmp_path / "target-root"
    target_root.mkdir()
    seen = []

    report = run_verify_install(
        ebuild, logdir=logdir,
        environment={"ROOT": str(target_root), "SYSROOT": str(target_root)},
        runner=_runner(logdir, seen, active_root=str(target_root)))

    assert report["ok"] is True
    assert report["environment"]["root"]["baseline"] == str(target_root)
    assert report["environment"]["root"]["value"] == str(target_root)
    assert ["portageq", "get_repo_path", str(target_root), "gentoo-zh"] in [
        args for args, _kwargs in seen]


def test_verify_install_preserves_baseline_keyword_files(tmp_path):
    logdir = tmp_path / "logs"
    seen = []
    runner = _runner(logdir, seen)
    keywords = tmp_path / "system-root" / "etc" / "portage" / \
        "package.accept_keywords"
    keywords.mkdir()
    (keywords / "local-policy").write_text(
        "app-misc/existing ~amd64\n", encoding="utf-8")

    report = run_verify_install(
        _ebuild(tmp_path), logdir=logdir, runner=runner)

    baseline = report["keyword_configuration"]["baseline"]
    assert report["ok"] is True
    assert baseline["kind"] == "directory"
    assert baseline["preserved"] is True
    assert len(baseline["temporary_links"]) == 1


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
    assert all("--pretend" not in args for args, _kwargs in seen)


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
    assert all("--pretend" not in args for args, _kwargs in seen)


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
    assert all("--pretend" not in args for args, _kwargs in seen)


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
    assert all("--pretend" not in args for args, _kwargs in seen)


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
    assert all("--pretend" not in args for args, _kwargs in seen)


def test_verify_install_timeout_is_incomplete_and_stops_before_merge(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail="pretend-timeout"), timeout=1)

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["timed_out"] is True
    assert res["truncated"] is True
    assert res["state"] == "timed-out"
    assert res["failed_step"] == "pretend"
    assert [step["name"] for step in res["steps"]] == ["pretend"]
    assert len([args for args, _kwargs in seen if "--pretend" in args]) == 1


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


def test_verify_install_keeps_binary_installed_dependencies_satisfied(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail="plan-new-dependency"))

    assert res["ok"] is True
    assert res["complete"] is True
    assert [step["name"] for step in res["steps"]] == ["pretend", "merge"]
    assert "dependency_elog" not in res
    assert {entry["step"] for entry in res["elog_files"]} <= {"merge"}
    assert len(res["plan"]["classified"]["new_dependency"]) == 1
    commands = [
        args for args, _kwargs in seen
        if args[0] == "emerge" and args != ["emerge", "--version"]]
    assert all("--onlydeps" not in command for command in commands)
    assert all("--usepkg=n" not in command for command in commands)
    assert all("--usepkg=y" in command for command in commands)


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


def test_verify_install_stops_when_pretend_fails(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail="pretend"))

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["failed_step"] == "pretend"
    assert len([args for args, _kwargs in seen if "--pretend" in args]) == 1


def test_verify_install_rejects_unrelated_upgrade_before_merge(tmp_path):
    logdir = tmp_path / "logs"
    seen = []

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, seen, fail="plan-unrelated"))

    assert res["ok"] is False
    assert res["complete"] is True
    assert res["failed_step"] == "plan-authorization"
    assert res["plan"]["classified"]["upgrade"][0]["package"] == (
        "dev-libs/unrelated")
    assert res["plan"]["unauthorized"][0]["package"] == "dev-libs/unrelated"
    assert [step["name"] for step in res["steps"]] == ["pretend"]


def test_verify_install_runs_explicitly_authorized_plan_expansion(tmp_path):
    logdir = tmp_path / "logs"

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, [], fail="plan-unrelated"),
        authorized_packages=["dev-libs/unrelated"])

    assert res["ok"] is True
    assert res["plan"]["authorized"] is True
    assert res["plan"]["classified"]["upgrade"][0]["authorized"] is True
    assert [step["name"] for step in res["steps"]] == ["pretend", "merge"]


def test_verify_install_rejects_incomplete_plan_before_merge(tmp_path):
    logdir = tmp_path / "logs"

    res = run_verify_install(
        _ebuild(tmp_path), logdir=logdir,
        runner=_runner(logdir, [], fail="plan-malformed"))

    assert res["ok"] is False
    assert res["complete"] is False
    assert res["failed_step"] == "plan"
    assert "does not match" in res["plan"]["errors"][0]
    assert [step["name"] for step in res["steps"]] == ["pretend"]


def test_emerge_plan_classifies_every_mutating_action():
    plan = parse_emerge_plan(
        _plan(
            "[binary   N    ] dev-libs/new-dependency-2::gentoo",
            "[ebuild   R    ] dev-libs/rebuilt-2::gentoo",
            "[binary   U    ] dev-libs/upgraded-3::gentoo [2::gentoo]",
            "[ebuild   D    ] dev-libs/downgraded-1::gentoo [2::gentoo]",
            TARGET_ACTION),
        ATOM,
        authorized_packages=[
            "dev-libs/rebuilt", "dev-libs/upgraded", "dev-libs/downgraded"],
    )

    assert plan["complete"] is True
    assert plan["authorized"] is True
    assert {
        category: [action["package"] for action in plan["classified"][category]]
        for category in (
            "target", "new_dependency", "rebuild", "upgrade", "downgrade")
    } == {
        "target": ["app-misc/foo"],
        "new_dependency": ["dev-libs/new-dependency"],
        "rebuild": ["dev-libs/rebuilt"],
        "upgrade": ["dev-libs/upgraded"],
        "downgrade": ["dev-libs/downgraded"],
    }


def test_emerge_plan_parses_slot_subslot_and_binary_build_id():
    plan = parse_emerge_plan(
        _plan(
            "[binary   N    ] "
            "dev-lang/python-3.14.6_p1-1:3.14/3.14::gentoo",
            TARGET_ACTION),
        ATOM,
    )

    dependency = plan["classified"]["new_dependency"][0]
    assert plan["complete"] is True
    assert dependency["package"] == "dev-lang/python"
    assert dependency["slot"] == "3.14"
    assert dependency["subslot"] == "3.14"
    assert dependency["source"] == "binary"


@pytest.mark.parametrize("row", [
    "[uninstall     ] dev-libs/obsolete-1",
    "[mystery       ] dev-libs/unknown-1",
])
def test_emerge_plan_rejects_uninstall_and_unknown_action_rows(row):
    text = _plan(TARGET_ACTION).replace(
        "\n\nTotal:", f"\n{row}\n\nTotal:")

    plan = parse_emerge_plan(text, ATOM)

    assert plan["complete"] is False
    assert plan["authorized"] is False
    assert plan["rejected_rows"][0]["line"] == row


def test_atom_rejects_other_repository(tmp_path):
    ebuild = _ebuild(tmp_path)
    (tmp_path / "profiles" / "repo_name").write_text("other\n")
    with pytest.raises(ValueError, match="gentoo-zh development checkout"):
        atom_from_ebuild(ebuild)
