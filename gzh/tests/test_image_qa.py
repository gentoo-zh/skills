import os
from pathlib import Path
from types import SimpleNamespace

from gzh.image_qa import inspect_image


def _runner(args, **kwargs):
    if args[0] == "desktop-file-validate":
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    if args[0] == "systemd-analyze":
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    raise AssertionError(args)


def test_image_inventory_is_deterministic(tmp_path):
    desktop = tmp_path / "usr" / "share" / "applications" / "demo.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("[Desktop Entry]\nType=Application\n", encoding="utf-8")
    (tmp_path / "usr" / "bin").mkdir(parents=True)
    (tmp_path / "usr" / "bin" / "demo").write_text("#!/bin/sh\n", encoding="utf-8")

    first = inspect_image(tmp_path, include_binaries=False, runner=_runner)
    second = inspect_image(tmp_path, include_binaries=False, runner=_runner)

    assert first["ok"] is True
    assert first["inventory_sha256"] == second["inventory_sha256"]
    assert first["counts"]["file"] == 2
    assert first["validators"][0]["kind"] == "desktop-entry"


def test_image_reports_privileged_and_world_writable_files(tmp_path):
    target = tmp_path / "tool"
    target.write_text("data", encoding="utf-8")
    target.chmod(0o4757)

    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)
    codes = {finding["code"] for finding in report["findings"]}

    assert report["ok"] is True
    assert {"privileged-mode", "world-writable"} <= codes


def test_image_rejects_escaping_symlink(tmp_path):
    (tmp_path / "usr").mkdir()
    (tmp_path / "usr" / "escape").symlink_to("../../outside")

    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)

    assert report["ok"] is False
    assert "escaping-symlink" in {finding["code"] for finding in report["findings"]}


def test_image_limit_is_incomplete(tmp_path):
    (tmp_path / "a").write_text("a", encoding="utf-8")
    (tmp_path / "b").write_text("b", encoding="utf-8")

    report = inspect_image(tmp_path, include_binaries=False, max_entries=1, runner=_runner)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["truncated"] is True


def test_validator_failure_is_an_error(tmp_path):
    desktop = tmp_path / "usr" / "share" / "applications" / "demo.desktop"
    desktop.parent.mkdir(parents=True)
    desktop.write_text("broken", encoding="utf-8")

    def runner(args, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="invalid desktop file")

    report = inspect_image(tmp_path, include_binaries=False, runner=runner)
    assert report["ok"] is False
    assert report["validators"][0]["stderr"] == "invalid desktop file"
