from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "install.sh"
UPDATER = ROOT / "update.sh"
INSTALLER_MODULE = ROOT / "scripts" / "install.py"
SKILL_NAMES = tuple(sorted(
    path.name for path in (ROOT / ".agents" / "skills").iterdir()
    if path.is_dir() and (path / "SKILL.md").is_file()))


def environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "HOME": str(tmp_path / "home"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "GZH_INSTALL_ROOT": str(tmp_path / "gzh-install"),
        "GZH_BIN_DIR": str(tmp_path / "bin"),
    })
    return env


def invoke(command: Path, args: list[str], env: dict[str, str],
           expected: int = 0) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [str(command), *args], cwd=ROOT, env=env,
        capture_output=True, text=True)
    assert proc.returncode == expected, proc.stdout + proc.stderr
    return proc


def load_installer():
    spec = importlib.util.spec_from_file_location("install_test", INSTALLER_MODULE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def installation_state(tmp_path: Path) -> Path:
    return tmp_path / "data" / "gentoo-zh-skills" / "skill-installations.json"


def previous_bundle(tmp_path: Path, mode: str) -> tuple[dict[str, str], Path, str, str]:
    env = environment(tmp_path)
    mode_arg = "--copy" if mode == "copy" else "--link"
    invoke(INSTALLER, ["codex", "--skills-only", mode_arg], env)
    state_path = installation_state(tmp_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    record = state["targets"][0]
    base = Path(record["target"])
    added = SKILL_NAMES[-1]
    retired = "retired-test-skill"
    if (base / added).is_symlink():
        (base / added).unlink()
    else:
        shutil.rmtree(base / added)
    retired_path = base / retired
    if mode == "link":
        retired_path.symlink_to(ROOT / ".agents" / "skills" / retired)
    else:
        retired_path.mkdir()
        marker = {
            "schema": 1,
            "installer": "gentoo-zh/skills",
            "skill": retired,
            "mode": "copy",
        }
        (retired_path / ".gzh-skill-install.json").write_text(
            json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        (retired_path / "old.txt").write_text("old\n", encoding="utf-8")
    record["skills"] = sorted(set(SKILL_NAMES) - {added} | {retired})
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return env, base, added, retired


@pytest.mark.parametrize("mode", ["copy", "link"])
def test_bundle_upgrade_status_and_refresh(tmp_path, mode):
    env, base, added, retired = previous_bundle(tmp_path, mode)

    status = invoke(
        INSTALLER, ["codex", "--skills-only", "--status"], env, expected=1)
    assert added in status.stdout
    assert "not installed" in status.stdout
    assert retired in status.stdout
    assert "retired" in status.stdout

    invoke(UPDATER, ["--installed-only"], env)

    assert (base / added).is_dir() or (base / added).is_symlink()
    assert not (base / retired).exists()
    assert not (base / retired).is_symlink()
    invoke(INSTALLER, ["codex", "--skills-only", "--status"], env)
    state = json.loads(installation_state(tmp_path).read_text(encoding="utf-8"))
    assert state["targets"][0]["skills"] == list(SKILL_NAMES)


@pytest.mark.parametrize("mode", ["copy", "link"])
def test_bundle_upgrade_uninstall_retires_removed_skill(tmp_path, mode):
    env, base, _added, retired = previous_bundle(tmp_path, mode)

    result = invoke(
        INSTALLER, ["codex", "--skills-only", "--uninstall"], env)

    assert retired in result.stdout
    assert not (base / retired).exists()
    assert not (base / retired).is_symlink()
    for name in set(SKILL_NAMES) - {_added}:
        assert not (base / name).exists()
        assert not (base / name).is_symlink()
    state = json.loads(installation_state(tmp_path).read_text(encoding="utf-8"))
    assert state["targets"] == []


@pytest.mark.parametrize("mode", ["copy", "link"])
def test_bundle_refresh_refuses_replaced_retired_skill_without_writes(tmp_path, mode):
    env, base, added, retired = previous_bundle(tmp_path, mode)
    retired_path = base / retired
    if retired_path.is_symlink():
        retired_path.unlink()
    else:
        shutil.rmtree(retired_path)
    retired_path.mkdir()
    sentinel = retired_path / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")

    result = invoke(UPDATER, ["--installed-only"], env, expected=1)

    assert "refusing to replace unowned path" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (base / added).exists()
    assert not (base / added).is_symlink()
    state = json.loads(installation_state(tmp_path).read_text(encoding="utf-8"))
    assert retired in state["targets"][0]["skills"]


@pytest.mark.parametrize("mode", ["copy", "link"])
def test_bundle_refresh_rolls_back_when_state_write_fails(
        tmp_path, mode, monkeypatch):
    env, base, added, retired = previous_bundle(tmp_path, mode)
    installer = load_installer()
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    records = installer.read_installation_state()
    old_record = records[base]
    new_record = installer.skill_bundle_record(
        base, old_record["clients"], old_record["mode"], SKILL_NAMES)
    monkeypatch.setattr(
        installer, "write_installation_state",
        lambda _records: (_ for _ in ()).throw(OSError("state write failed")))

    with pytest.raises(OSError, match="state write failed"):
        installer.synchronize_skill_bundle(records, old_record, new_record)

    assert not (base / added).exists()
    assert not (base / added).is_symlink()
    assert (base / retired).exists() or (base / retired).is_symlink()
    state = json.loads(installation_state(tmp_path).read_text(encoding="utf-8"))
    assert retired in state["targets"][0]["skills"]


def test_default_clients_use_separate_codex_and_opencode_paths_when_configured(tmp_path):
    env = environment(tmp_path)
    invoke(INSTALLER, ["--skills-only"], env)

    for name in SKILL_NAMES:
        assert (tmp_path / "codex" / "skills" / name).is_symlink()
        assert (tmp_path / "xdg" / "opencode" / "skills" / name).is_symlink()
        assert not (tmp_path / "claude" / "skills" / name).exists()
    status = invoke(INSTALLER, ["--skills-only", "--status"], env)
    assert "codex" in status.stdout
    assert "opencode" in status.stdout
    assert status.stdout.count("link, current") == 2 * len(SKILL_NAMES)


def test_default_clients_share_official_agents_scope(tmp_path):
    env = environment(tmp_path)
    env.pop("CODEX_HOME")
    invoke(INSTALLER, ["--skills-only"], env)

    for name in SKILL_NAMES:
        assert (tmp_path / "home" / ".agents" / "skills" / name).is_symlink()
        assert not (tmp_path / "home" / ".claude" / "skills" / name).exists()
        assert not (tmp_path / "xdg" / "opencode" / "skills" / name).exists()
    status = invoke(INSTALLER, ["--skills-only", "--status"], env)
    assert "codex+opencode" in status.stdout
    assert status.stdout.count("link, current") == len(SKILL_NAMES)


def test_all_clients_refuse_planned_opencode_duplicates_before_writes(tmp_path):
    env = environment(tmp_path)
    env.pop("CODEX_HOME")

    result = invoke(
        INSTALLER, ["codex", "claude", "opencode", "--skills-only"],
        env, expected=1)

    assert "would create duplicate OpenCode skills" in result.stderr
    assert not (tmp_path / "home" / ".agents" / "skills").exists()
    assert not (tmp_path / "claude" / "skills").exists()


def test_codex_default_uses_official_user_scope(tmp_path):
    env = environment(tmp_path)
    env.pop("CODEX_HOME")
    invoke(INSTALLER, ["codex", "--skills-only"], env)

    for name in SKILL_NAMES:
        assert (tmp_path / "home" / ".agents" / "skills" / name).is_symlink()
        assert not (tmp_path / "home" / ".codex" / "skills" / name).exists()


def test_codex_install_refuses_legacy_directory_duplicate(tmp_path):
    env = environment(tmp_path)
    env.pop("CODEX_HOME")
    legacy = tmp_path / "home" / ".codex" / "skills" / SKILL_NAMES[0]
    legacy.mkdir(parents=True)

    result = invoke(INSTALLER, ["codex", "--skills-only"], env, expected=1)

    assert "duplicate Codex skill" in result.stderr
    for name in SKILL_NAMES:
        assert not (tmp_path / "home" / ".agents" / "skills" / name).exists()


def test_legacy_codex_copy_remains_refreshable(tmp_path):
    env = environment(tmp_path)
    env["CODEX_HOME"] = str(tmp_path / "home" / ".codex")
    invoke(INSTALLER, ["codex", "--skills-only", "--copy"], env)
    copied = (
        tmp_path / "home" / ".codex" / "skills" / SKILL_NAMES[0] / "SKILL.md")
    copied.write_text("stale\n", encoding="utf-8")
    env.pop("CODEX_HOME")

    invoke(UPDATER, ["--installed-only"], env)

    assert copied.read_text(encoding="utf-8") == (
        ROOT / ".agents" / "skills" / SKILL_NAMES[0] / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_opencode_only_uses_native_path(tmp_path):
    env = environment(tmp_path)
    invoke(INSTALLER, ["opencode", "--skills-only", "--copy"], env)
    for name in SKILL_NAMES:
        destination = tmp_path / "xdg" / "opencode" / "skills" / name
        assert destination.is_dir()
        assert (destination / ".gzh-skill-install.json").is_file()
    status = invoke(INSTALLER, ["--skills-only", "--status"], env, expected=1)
    assert str(tmp_path / "xdg" / "opencode" / "skills") in status.stdout
    invoke(INSTALLER, ["--skills-only", "--uninstall"], env)
    for name in SKILL_NAMES:
        assert not (tmp_path / "xdg" / "opencode" / "skills" / name).exists()


def test_opencode_install_refuses_cross_directory_duplicates(tmp_path):
    env = environment(tmp_path)
    env.pop("CODEX_HOME")
    invoke(INSTALLER, ["opencode", "--skills-only"], env)
    result = invoke(INSTALLER, ["--skills-only"], env, expected=1)
    assert "duplicate OpenCode skill" in result.stderr
    for name in SKILL_NAMES:
        assert not (tmp_path / "home" / ".agents" / "skills" / name).exists()


def test_copy_status_refresh_and_uninstall(tmp_path):
    env = environment(tmp_path)
    invoke(INSTALLER, ["codex", "--skills-only", "--copy"], env)
    copied = tmp_path / "codex" / "skills" / SKILL_NAMES[0] / "SKILL.md"
    copied.write_text("stale\n", encoding="utf-8")
    status = invoke(
        INSTALLER, ["codex", "--skills-only", "--status"], env, expected=1)
    assert "copy, stale" in status.stdout

    invoke(UPDATER, ["--installed-only"], env)
    assert copied.read_text(encoding="utf-8") == (
        ROOT / ".agents" / "skills" / SKILL_NAMES[0] / "SKILL.md"
    ).read_text(encoding="utf-8")
    invoke(INSTALLER, ["codex", "--skills-only", "--uninstall"], env)
    assert not copied.parent.exists()


def test_unowned_destination_is_never_replaced(tmp_path):
    env = environment(tmp_path)
    destination = tmp_path / "codex" / "skills" / SKILL_NAMES[0]
    destination.mkdir(parents=True)
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep\n", encoding="utf-8")
    invoke(INSTALLER, ["codex", "--skills-only"], env, expected=1)
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (destination.parent / SKILL_NAMES[1]).exists()
    invoke(
        INSTALLER, ["codex", "--skills-only", "--uninstall"], env,
        expected=1)
    assert sentinel.is_file()


def test_gzh_install_status_and_uninstall(tmp_path):
    env = environment(tmp_path)
    invoke(INSTALLER, ["--gzh-only"], env)
    executable = tmp_path / "bin" / "gzh"
    assert executable.is_symlink()
    invoke(INSTALLER, ["--gzh-only", "--status"], env)
    help_result = subprocess.run(
        [str(executable), "--help"], env=env, capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "Usage:" in help_result.stdout
    invoke(INSTALLER, ["--gzh-only", "--uninstall"], env)
    assert not executable.exists()
    assert not (tmp_path / "gzh-install").exists()


def test_gzh_install_refuses_dangling_unowned_root_symlink(tmp_path):
    env = environment(tmp_path)
    install_root = tmp_path / "gzh-install"
    install_root.symlink_to(tmp_path / "missing-user-target")

    invoke(INSTALLER, ["--gzh-only"], env, expected=1)

    assert install_root.is_symlink()
    assert install_root.readlink() == tmp_path / "missing-user-target"
    assert not (tmp_path / "bin" / "gzh").exists()
