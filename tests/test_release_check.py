from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "release_check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("release_check_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


release_check = load_module()


def fixture_repository(tmp_path, *, project_version="0.1.0",
                       init_version="0.1.0", licensed=False):
    package = tmp_path / "gzh" / "gzh"
    package.mkdir(parents=True)
    license_field = '\nlicense = {text = "Proprietary"}' if licensed else ""
    (tmp_path / "gzh" / "pyproject.toml").write_text(
        "[project]\n"
        "name = \"gzh\"\n"
        f"version = \"{project_version}\"{license_field}\n",
        encoding="utf-8")
    (package / "__init__.py").write_text(
        f'__version__ = "{init_version}"\n', encoding="utf-8")
    (tmp_path / "RELEASING.md").write_text(
        "# Release Contract\n", encoding="utf-8")
    for relative in release_check.PLUGIN_MANIFESTS.values():
        manifest = tmp_path / relative
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(
            json.dumps({"version": project_version}), encoding="utf-8")
    if licensed:
        (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    return tmp_path


def initialize_repository(path):
    environment = os.environ.copy()
    environment.update({
        "GIT_AUTHOR_NAME": "Release Test",
        "GIT_AUTHOR_EMAIL": "release@example.invalid",
        "GIT_COMMITTER_NAME": "Release Test",
        "GIT_COMMITTER_EMAIL": "release@example.invalid",
    })
    subprocess.run(["git", "init", "-q"], cwd=path, env=environment, check=True)
    subprocess.run(["git", "add", "."], cwd=path, env=environment, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"],
        cwd=path, env=environment, check=True)
    return environment


def test_current_source_only_release_contract_is_consistent():
    report = release_check.release_report(
        root=ROOT, tag="v0.3.0", mode="source-only")

    assert report["ok"] is True
    assert report["version_declarations"] == {
        "pyproject": "0.3.0",
        "package": "0.3.0",
        "cli": "0.3.0",
        "codex_plugin": "0.3.0",
        "claude_plugin": "0.3.0",
    }
    assert report["rights"]["status"] == "undeclared"
    assert report["custom_package_artifacts_allowed"] is False
    assert report["update_channel"] == "master"


def test_release_contract_rejects_version_and_tag_mismatch(tmp_path):
    repository = fixture_repository(tmp_path, init_version="0.2.0")

    report = release_check.release_report(
        root=repository, tag="v0.2.0", mode="source-only",
        observed_cli_version="0.1.0")

    assert report["ok"] is False
    assert report["errors"] == [
        "version declarations do not match",
        "release tag must be v0.1.0",
    ]


def test_release_contract_rejects_plugin_version_drift(tmp_path):
    repository = fixture_repository(tmp_path)
    manifest = repository / release_check.PLUGIN_MANIFESTS["codex_plugin"]
    manifest.write_text(json.dumps({"version": "0.2.0"}), encoding="utf-8")

    report = release_check.release_report(
        root=repository, tag="v0.1.0", mode="source-only",
        observed_cli_version="0.1.0")

    assert report["ok"] is False
    assert report["errors"] == ["version declarations do not match"]


def test_package_artifacts_require_both_license_surfaces(tmp_path):
    repository = fixture_repository(tmp_path)

    denied = release_check.release_report(
        root=repository, tag="v0.1.0", mode="package",
        observed_cli_version="0.1.0")
    declared_repository = fixture_repository(
        tmp_path / "licensed", licensed=True)
    declared = release_check.release_report(
        root=declared_repository, tag="v0.1.0", mode="package",
        observed_cli_version="0.1.0")

    assert denied["ok"] is False
    assert denied["custom_package_artifacts_allowed"] is False
    assert denied["rights"]["reviewed_decision"] is False
    assert declared["ok"] is False
    assert declared["rights"]["status"] == "declared-unreviewed"
    assert declared["rights"]["reviewed_decision"] is False
    assert declared["custom_package_artifacts_allowed"] is False
    assert any(
        "deterministic rights decision contract" in error
        for error in declared["errors"])


def test_release_contract_requires_annotated_tag_at_head(tmp_path):
    repository = fixture_repository(tmp_path)
    environment = initialize_repository(repository)
    subprocess.run(
        ["git", "tag", "v0.1.0"], cwd=repository,
        env=environment, check=True)

    lightweight = release_check.release_report(
        root=repository, tag="v0.1.0", mode="source-only",
        observed_cli_version="0.1.0", require_annotated_tag=True)

    assert lightweight["ok"] is False
    assert lightweight["tag_verification"]["object_type"] == "commit"
    assert "release tag v0.1.0 must be annotated" in lightweight["errors"]

    subprocess.run(
        ["git", "tag", "-d", "v0.1.0"], cwd=repository,
        env=environment, check=True, capture_output=True)
    subprocess.run(
        ["git", "tag", "-a", "v0.1.0", "-m", "v0.1.0"],
        cwd=repository, env=environment, check=True)
    annotated = release_check.release_report(
        root=repository, tag="v0.1.0", mode="source-only",
        observed_cli_version="0.1.0", require_annotated_tag=True)

    assert annotated["ok"] is True
    assert annotated["tag_verification"]["object_type"] == "tag"
    assert annotated["tag_verification"]["target"] == (
        annotated["tag_verification"]["head"])

    (repository / "change").write_text("later\n", encoding="utf-8")
    subprocess.run(["git", "add", "change"], cwd=repository,
                   env=environment, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "later"],
        cwd=repository, env=environment, check=True)
    moved_head = release_check.release_report(
        root=repository, tag="v0.1.0", mode="source-only",
        observed_cli_version="0.1.0", require_annotated_tag=True)

    assert moved_head["ok"] is False
    assert "release tag v0.1.0 does not point to HEAD" in moved_head["errors"]


def test_release_check_cli_emits_structured_result():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--mode", "source-only",
         "--tag", "v0.3.0"],
        cwd=ROOT, check=False, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["expected_tag"] == "v0.3.0"
    assert result.stderr == ""
