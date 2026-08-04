from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "gentoo_integration.py"
WORKFLOW = ROOT / ".github" / "workflows" / "gentoo-integration.yml"
IMAGE_LOCK = ROOT / "integration" / "gentoo" / "image-lock.json"


def load_module():
    spec = importlib.util.spec_from_file_location("gentoo_integration_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


integration = load_module()


def command(returncode=0, *, complete=True, timed_out=False, truncated=False):
    return {
        "returncode": returncode,
        "complete": complete,
        "timed_out": timed_out,
        "truncated": truncated,
    }


def test_fixture_contract_and_cli_validate_without_root():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--validate-only"],
        cwd=ROOT, check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["case_ids"] == ["good-install", "qa-elog"]
    assert "@sha256:" in report["image_reference"]
    assert len(report["fixture_tree_sha256"]) == 64


def test_evidence_gate_accepts_only_clean_success():
    case = {
        "artifact_sha256": "a" * 64,
        "expected_gate": "accept",
        "expected_elog_pattern": None,
    }
    artifact = {
        "exists": True,
        "regular": True,
        "executable": True,
        "vdb_contents_exists": True,
        "sha256": "a" * 64,
    }

    decision = integration.evaluate_case(
        case, [command(), command()], artifact, [], command())
    with_elog = integration.evaluate_case(
        case, [command()], artifact,
        [{"content": "[QA] unexpected"}], command())

    assert decision == {
        "phase_commands_ok": True,
        "artifact_ok": True,
        "actual_gate": "accept",
        "expected_gate": "accept",
        "boundary_verified": False,
        "cleanup_ok": True,
        "matched": True,
    }
    assert with_elog["actual_gate"] == "reject"
    assert with_elog["matched"] is False


def test_known_bad_boundary_requires_successful_phase_artifact_and_qa_elog():
    case = {
        "artifact_sha256": "b" * 64,
        "expected_gate": "reject",
        "expected_elog_pattern": "intentional boundary",
    }
    artifact = {
        "exists": True,
        "regular": True,
        "executable": True,
        "vdb_contents_exists": True,
        "sha256": "b" * 64,
    }

    expected = integration.evaluate_case(
        case, [command()], artifact,
        [{"content": "[QA] intentional boundary"}], command())
    unrelated_failure = integration.evaluate_case(
        case, [command(returncode=1)], artifact, [], command())

    assert expected["actual_gate"] == "reject"
    assert expected["boundary_verified"] is True
    assert expected["matched"] is True
    assert unrelated_failure["actual_gate"] == "reject"
    assert unrelated_failure["boundary_verified"] is False
    assert unrelated_failure["matched"] is False


def test_source_merge_command_forces_exact_source_without_usepkgonly():
    command_line = integration.source_merge_command({
        "atom": "test-fixture/example-1.0"})

    assert command_line == [
        "emerge", "--oneshot", "--selective=n", "--nodeps",
        "--usepkg=n", "--verbose",
        "=test-fixture/example-1.0::gentoo-zh",
    ]
    assert "--usepkgonly=n" not in command_line


def test_verifier_decision_requires_the_production_elog_boundary():
    accept = {"expected_gate": "accept", "expected_elog_pattern": None}
    reject = {
        "expected_gate": "reject",
        "expected_elog_pattern": "intentional boundary",
    }

    accepted = integration.evaluate_verifier(accept, {
        "complete": True, "ok": True, "failed_step": None,
        "elog_files": [],
    })
    rejected = integration.evaluate_verifier(reject, {
        "complete": True, "ok": False, "failed_step": "elog",
        "elog_files": [{"text": "QA: intentional boundary"}],
    })
    wrong_failure = integration.evaluate_verifier(reject, {
        "complete": True, "ok": False, "failed_step": "merge",
        "elog_files": [],
    })

    assert accepted["matched"] is True
    assert rejected["matched"] is True
    assert wrong_failure["matched"] is False


def test_fixture_manifest_rejects_path_escape(tmp_path):
    outside = tmp_path / "outside.ebuild"
    outside.write_text("EAPI=8\n", encoding="utf-8")
    fixture_root = tmp_path / "fixtures"
    fixture_root.mkdir()
    data = {
        "schema": 1,
        "scope": "One fixture; not an overlay matrix",
        "cases": [{
            "id": "accept",
            "atom": "test-fixture/example-1",
            "ebuild": "../outside.ebuild",
            "installed_path": "usr/bin/example",
            "artifact_sha256": "a" * 64,
            "expected_gate": "accept",
            "expected_elog_pattern": None,
        }, {
            "id": "reject",
            "atom": "test-fixture/example-bad-1",
            "ebuild": "../outside.ebuild",
            "installed_path": "usr/bin/example-bad",
            "artifact_sha256": "b" * 64,
            "expected_gate": "reject",
            "expected_elog_pattern": "expected",
        }],
    }

    with pytest.raises(
            integration.IntegrationError,
            match="must remain below the fixture root"):
        integration.validate_manifest(data, fixture_root)


def test_elog_inventory_rejects_symlinks_and_oversized_evidence(tmp_path):
    elog = tmp_path / "elog"
    elog.mkdir()
    target = tmp_path / "target.log"
    target.write_text("outside\n", encoding="utf-8")
    (elog / "linked.log").symlink_to(target)

    with pytest.raises(integration.IntegrationError, match="not a regular file"):
        integration.elog_inventory(tmp_path)

    (elog / "linked.log").unlink()
    (elog / "large.log").write_bytes(
        b"x" * (integration.MAX_ELOG_TOTAL_BYTES + 1))
    with pytest.raises(integration.IntegrationError, match="exceeds"):
        integration.elog_inventory(tmp_path)


def test_elog_inventory_rejects_path_replacement(tmp_path, monkeypatch):
    elog = tmp_path / "elog"
    elog.mkdir()
    entry = elog / "entry.log"
    entry.write_text("original\n", encoding="utf-8")
    replacement = tmp_path / "replacement.log"
    replacement.write_text("replacement\n", encoding="utf-8")
    real_open = os.open
    replaced = False

    def replacing_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == "entry.log" and kwargs.get("dir_fd") is not None and not replaced:
            os.replace(replacement, entry)
            replaced = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(integration.os, "open", replacing_open)

    with pytest.raises(integration.IntegrationError, match="identity changed"):
        integration.elog_inventory(tmp_path)


def test_elog_inventory_rejects_growth_during_bounded_read(tmp_path, monkeypatch):
    elog = tmp_path / "elog"
    elog.mkdir()
    entry = elog / "entry.log"
    entry.write_bytes(b"original\n")
    real_read = os.read
    grown = False

    def growing_read(file_descriptor, maximum):
        nonlocal grown
        chunk = real_read(file_descriptor, maximum)
        if chunk and not grown:
            with entry.open("ab") as handle:
                handle.write(b"x" * integration.MAX_ELOG_TOTAL_BYTES)
            grown = True
        return chunk

    monkeypatch.setattr(integration.os, "read", growing_read)

    with pytest.raises(integration.IntegrationError, match="exceeds"):
        integration.elog_inventory(tmp_path)


def test_workflow_is_scoped_isolated_pinned_and_preserves_evidence():
    text = WORKFLOW.read_text(encoding="utf-8")
    lock = json.loads(IMAGE_LOCK.read_text(encoding="utf-8"))

    assert "workflow_dispatch:" in text
    assert "push:" in text
    assert "gzh/gzh/qa_evidence.py" in text
    assert "gzh/gzh/verify_install.py" in text
    assert "scripts/gentoo_integration.py" in text
    assert "schedule:" not in text
    assert "pull_request:" not in text
    assert f"image: {lock['reference']}" in text
    assert f"GZH_GENTOO_IMAGE: {lock['reference']}" in text
    assert "permissions:\n  contents: read" in text
    assert "timeout-minutes: 30" in text
    assert "if: always()" in text
    assert "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in text
    assert ("uses: actions/upload-artifact@"
            "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a") in text
    assert "if-no-files-found: error" in text
    assert "gentoo-integration-output/" in text
