from pathlib import Path

import pytest
from click.testing import CliRunner

from gzh.cli import cli
from gzh.state import batch_dir, queue_dir, state_dir, triage_log


def test_state_dir_uses_override():
    env = {"GZH_STATE_DIR": "/tmp/gzh-state", "XDG_STATE_HOME": "/ignored"}
    assert state_dir(env) == Path("/tmp/gzh-state")
    assert queue_dir(env) == Path("/tmp/gzh-state/queues")
    assert batch_dir(env) == Path("/tmp/gzh-state/batches")
    assert triage_log(env) == Path("/tmp/gzh-state/triage/skip-log.jsonl")


def test_state_dir_uses_xdg_state_home():
    assert state_dir({"XDG_STATE_HOME": "/tmp/xdg-state"}) == \
        Path("/tmp/xdg-state/gentoo-zh-skills")


def test_state_dir_rejects_relative_override():
    with pytest.raises(ValueError, match="absolute"):
        state_dir({"GZH_STATE_DIR": "relative/state"})


def test_state_dir_ignores_relative_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert state_dir({"XDG_STATE_HOME": "relative/state"}) == \
        tmp_path / ".local" / "state" / "gentoo-zh-skills"


def test_state_dir_command(monkeypatch, tmp_path):
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    result = CliRunner().invoke(cli, ["state-dir"])
    assert result.exit_code == 0
    assert result.output.strip() == str(tmp_path)


def test_state_dir_command_refuses_overlay_child(monkeypatch, tmp_path):
    state = tmp_path / "state"
    monkeypatch.setenv("GZH_STATE_DIR", str(state))
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    result = CliRunner().invoke(cli, ["state-dir"])
    assert result.exit_code == 1
    assert "outside the overlay" in result.output
