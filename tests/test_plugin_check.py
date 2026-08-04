from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "plugin_check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("plugin_check_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


plugin_check = load_module()


def fixture_package(tmp_path: Path) -> Path:
    for relative in (
        ".agents/.codex-plugin/plugin.json",
        ".agents/.claude-plugin/plugin.json",
        ".agents/plugins/marketplace.json",
        ".claude-plugin/marketplace.json",
    ):
        source = ROOT / relative
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    skill = tmp_path / ".agents/skills/example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: example\ndescription: Example skill.\n---\n",
        encoding="utf-8")
    return tmp_path


def rewrite_json(path: Path, update) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    update(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_current_plugin_package_contract_is_consistent():
    report = plugin_check.plugin_report(ROOT)

    assert report["ok"] is True
    assert report["plugin"] == "gentoo-overlay-skills"
    assert report["marketplace"] == "gentoo-zh-skills"
    assert report["versions"] == {"codex": "0.3.0", "claude": "0.3.0"}
    assert report["skills"] == [
        "gentoo-overlay-development",
        "gzh-bump-from-issues",
        "gzh-maintain-skills",
        "gzh-version-bump",
    ]


def test_plugin_package_rejects_skill_path_escape(tmp_path):
    root = fixture_package(tmp_path)
    manifest = root / ".agents/.codex-plugin/plugin.json"
    rewrite_json(manifest, lambda data: data.update(skills="./../outside"))

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert "Codex manifest skills escapes the plugin root" in report["errors"]


def test_plugin_package_rejects_missing_manifest(tmp_path):
    root = fixture_package(tmp_path)
    (root / ".agents/.codex-plugin/plugin.json").unlink()

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert any(error.startswith("invalid JSON ") for error in report["errors"])


def test_plugin_package_rejects_marketplace_source_drift(tmp_path):
    root = fixture_package(tmp_path)
    marketplace = root / ".agents/plugins/marketplace.json"
    rewrite_json(
        marketplace,
        lambda data: data["plugins"][0]["source"].update(path="./other"))

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert "Codex marketplace must use the canonical local plugin root" in report["errors"]


def test_plugin_package_rejects_inferred_license(tmp_path):
    root = fixture_package(tmp_path)
    (root / "LICENSE").write_text("unreviewed\n", encoding="utf-8")
    manifest = root / ".agents/.claude-plugin/plugin.json"
    rewrite_json(manifest, lambda data: data.update(license="MIT"))

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert "Claude manifest must not infer a repository license" in report["errors"]


def test_plugin_package_rejects_cross_client_version_drift(tmp_path):
    root = fixture_package(tmp_path)
    manifest = root / ".agents/.claude-plugin/plugin.json"
    rewrite_json(manifest, lambda data: data.update(version="0.4.0"))

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert "Codex and Claude plugin versions do not match" in report["errors"]


def test_plugin_package_rejects_oversized_codex_subtitle(tmp_path):
    root = fixture_package(tmp_path)
    manifest = root / ".agents/.codex-plugin/plugin.json"
    rewrite_json(
        manifest,
        lambda data: data["interface"].update(shortDescription="x" * 31))

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert "Codex shortDescription must contain 1 to 30 characters" in report["errors"]


def test_plugin_package_rejects_symlinks(tmp_path):
    root = fixture_package(tmp_path)
    target = root / ".agents/skills/example/link"
    target.symlink_to("SKILL.md")

    report = plugin_check.plugin_report(root)

    assert report["ok"] is False
    assert "plugin payload contains a symlink: skills/example/link" in report["errors"]
