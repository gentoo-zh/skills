import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import gzh.image_qa as image_mod
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
    assert "entries" not in first


def test_image_reports_privileged_and_world_writable_files(tmp_path):
    target = tmp_path / "tool"
    target.write_text("#!/bin/sh\n", encoding="utf-8")
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


def test_validator_aggregate_tool_budget_fails_closed(tmp_path, monkeypatch):
    applications = tmp_path / "usr" / "share" / "applications"
    applications.mkdir(parents=True)
    for name in ("one.desktop", "two.desktop"):
        (applications / name).write_text(
            "[Desktop Entry]\nType=Application\n", encoding="utf-8")
    monkeypatch.setattr(image_mod, "MAX_IMAGE_TOOL_COMMANDS", 1)

    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["validator_scope_complete"] is False
    assert report["tool_budget"]["exhausted"] is True
    assert report["tool_budget"]["reason"] == "command-limit"
    assert report["finding_counts"]["by_code"]["tool-budget-exhausted"] == 1


def test_inventory_evidence_is_relative_owned_and_hashed(tmp_path, monkeypatch):
    image = tmp_path / "image"
    image.mkdir()
    (image / "file").write_text("data", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = inspect_image(
        image,
        include_binaries=False,
        inventory_evidence=Path("inventory.json"),
        runner=_runner,
    )

    evidence = tmp_path / "inventory.json"
    payload = evidence.read_bytes()
    assert report["inventory"] == {
        "error": None,
        "path": "inventory.json",
        "requested": True,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "written": True,
    }
    assert evidence.stat().st_uid == os.geteuid()
    assert evidence.stat().st_mode & 0o777 == 0o600
    assert list(json.loads(payload)) == [
        "schema_version", "root", "complete", "truncated", "findings",
        "entries"]
    assert list(report).index("findings") < list(report).index("inventory")
    assert "entries" not in report


def test_inventory_write_failure_marks_report_incomplete(tmp_path, monkeypatch):
    (tmp_path / "file").write_text("data", encoding="utf-8")

    def fail_write(path, payload, **kwargs):
        raise OSError("evidence storage unavailable")

    monkeypatch.setattr(image_mod, "_write_inventory_evidence", fail_write)
    report = inspect_image(
        tmp_path,
        include_binaries=False,
        inventory_evidence=Path("inventory.json"),
        runner=_runner,
    )

    assert report["complete"] is False
    assert report["ok"] is False
    assert report["inventory"]["written"] is False
    assert report["finding_counts"]["by_code"]["inventory-write-failed"] == 1


def test_inventory_records_incomplete_binary_scope(tmp_path, monkeypatch):
    (tmp_path / "program").write_bytes(b"\x7fELFpayload")
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setattr(image_mod, "inspect_binaries", lambda *args, **kwargs: {
        "complete": False,
        "findings": [],
        "ok": False,
        "scanned": 1,
        "truncated": True,
    })

    report = inspect_image(
        tmp_path,
        inventory_evidence=Path("inventory.json"),
        runner=_runner,
    )
    inventory = json.loads((tmp_path.parent / "inventory.json").read_text())

    assert report["complete"] is False
    assert report["truncated"] is True
    assert inventory["complete"] is False
    assert inventory["truncated"] is True


def test_inventory_evidence_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    image = tmp_path / "image"
    image.mkdir()
    evidence = tmp_path / "inventory.json"
    evidence.write_text("keep\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    report = inspect_image(
        image,
        include_binaries=False,
        inventory_evidence=Path("inventory.json"),
        runner=_runner,
    )

    assert report["complete"] is False
    assert report["inventory"]["written"] is False
    assert evidence.read_text(encoding="utf-8") == "keep\n"


def test_inventory_evidence_rejects_absolute_path(tmp_path):
    report = inspect_image(
        tmp_path,
        include_binaries=False,
        inventory_evidence=tmp_path / "inventory.json",
        runner=_runner,
    )

    assert report["complete"] is False
    assert report["inventory"]["written"] is False
    assert "must be relative" in report["inventory"]["error"]


def test_inventory_evidence_rejects_path_inside_image(tmp_path, monkeypatch):
    image = tmp_path / "image"
    image.mkdir()
    monkeypatch.chdir(tmp_path)

    report = inspect_image(
        image,
        include_binaries=False,
        inventory_evidence=Path("image/inventory.json"),
        runner=_runner,
    )

    assert report["complete"] is False
    assert report["inventory"]["written"] is False
    assert not (image / "inventory.json").exists()
    assert "outside the inspected image" in report["inventory"]["error"]


def test_unreadable_walk_is_incomplete(tmp_path, monkeypatch):
    def failing_walk(root, **kwargs):
        kwargs["onerror"](PermissionError(13, "permission denied", root / "hidden"))
        return []

    monkeypatch.setattr(image_mod.os, "walk", failing_walk)
    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)

    assert report["complete"] is False
    assert report["ok"] is False
    assert report["finding_counts"]["by_code"]["unreadable-path"] == 1


def test_executable_regular_files_are_classified(tmp_path):
    elf = tmp_path / "program"
    elf.write_bytes(b"\x7fELFpayload")
    script = tmp_path / "launcher"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    data = tmp_path / "payload.dat"
    data.write_bytes(b"not executable content")
    for path in (elf, script, data):
        path.chmod(0o755)

    report = inspect_image(
        tmp_path,
        include_binaries=False,
        executable_allowlist=["/payload.dat"],
        runner=_runner,
    )

    assert report["ok"] is True
    assert report["summary"]["executables"]["by_kind"] == {
        "elf": 1, "script": 1, "unexpected-data": 1}
    assert report["summary"]["executables"]["total"] == 3
    assert report["summary"]["executables"]["unexpected"]["samples"] == [
        "/payload.dat"]


def test_unexpected_executable_outside_resources_is_a_warning(tmp_path):
    data = tmp_path / "payload.dat"
    data.write_bytes(b"not executable content")
    data.chmod(0o755)

    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)

    assert report["ok"] is True
    assert report["finding_counts"]["by_code"]["unexpected-executable"] == 1
    assert report["finding_counts"]["by_severity"]["warning"] >= 1


def test_resource_executable_requires_exact_allowlist(tmp_path):
    resource = tmp_path / "usr" / "share" / "demo" / "font.dat"
    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"font data")
    resource.chmod(0o755)

    rejected = inspect_image(tmp_path, include_binaries=False, runner=_runner)
    accepted = inspect_image(
        tmp_path,
        include_binaries=False,
        executable_allowlist=["/usr/share/demo/font.dat"],
        runner=_runner,
    )

    assert rejected["ok"] is False
    assert rejected["finding_counts"]["by_code"][
        "unexplained-resource-executable"] == 1
    assert accepted["ok"] is True
    assert accepted["finding_counts"]["by_code"]["allowlisted-executable"] == 1


def test_non_elf_allowlist_can_be_required_for_prebuilt_images(tmp_path):
    script = tmp_path / "usr" / "bin" / "launcher"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)

    report = inspect_image(
        tmp_path,
        include_binaries=False,
        require_non_elf_allowlist=True,
        runner=_runner,
    )

    assert report["ok"] is False
    assert report["finding_counts"]["by_code"][
        "unallowlisted-non-elf-executable"] == 1
    assert report["finding_counts"]["by_code"][
        "inventory-evidence-required"] == 1


def test_prebuilt_strict_mode_requires_durable_inventory(tmp_path, monkeypatch):
    image = tmp_path / "image"
    image.mkdir()
    (image / "file").write_text("data", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    missing = inspect_image(
        image,
        include_binaries=False,
        require_non_elf_allowlist=True,
        runner=_runner,
    )
    recorded = inspect_image(
        image,
        include_binaries=False,
        require_non_elf_allowlist=True,
        inventory_evidence=Path("inventory.json"),
        runner=_runner,
    )

    assert missing["ok"] is False
    assert missing["complete"] is False
    assert recorded["ok"] is True
    assert recorded["inventory"]["written"] is True


def test_finding_samples_are_bounded_by_code(tmp_path):
    resources = tmp_path / "resources"
    resources.mkdir()
    for index in range(12):
        path = resources / f"data-{index:02d}"
        path.write_bytes(b"data")
        path.chmod(0o755)

    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)

    samples = [
        finding for finding in report["findings"]
        if finding["code"] == "unexplained-resource-executable"
    ]
    assert len(samples) == image_mod.MAX_FINDING_SAMPLES_PER_CODE
    assert report["finding_counts"]["by_code"][
        "unexplained-resource-executable"] == 12
    assert report["findings_truncated"] is True


def test_symlink_modes_are_excluded_and_target_states_are_separate(tmp_path):
    (tmp_path / "usr").mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.write_text("target", encoding="utf-8")
    (tmp_path / "usr" / "absolute").symlink_to("/missing")
    (tmp_path / "usr" / "escape").symlink_to(f"../../{outside.name}")

    report = inspect_image(tmp_path, include_binaries=False, runner=_runner)
    codes = report["finding_counts"]["by_code"]

    assert "world-writable" not in codes
    assert codes["absolute-symlink"] == 1
    assert codes["broken-symlink"] == 1
    assert codes["escaping-symlink"] == 1
    assert "symlink" not in report["summary"]["mode_counts"]
    assert report["summary"]["symlinks"]["absolute"]["count"] == 1
    assert report["summary"]["symlinks"]["broken"]["count"] == 1
    assert report["summary"]["symlinks"]["escaping"]["count"] == 1
