#!/usr/bin/env python3
"""Validate the repository's cross-client plugin package contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PLUGIN_ROOT = Path(".agents")
PLUGIN_NAME = "gentoo-overlay-skills"
MARKETPLACE_NAME = "gentoo-zh-skills"
SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?")


def load_object(path: Path, errors: list[str]) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON {path}: {exc}")
        return {}
    if not isinstance(value, dict):
        errors.append(f"JSON root must be an object: {path}")
        return {}
    return value


def relative_path(root: Path, raw: object, label: str,
                  errors: list[str]) -> Path | None:
    if not isinstance(raw, str) or not raw.startswith("./"):
        errors.append(f"{label} must be a ./-prefixed path")
        return None
    target = (root / raw).resolve()
    if not target.is_relative_to(root.resolve()):
        errors.append(f"{label} escapes the plugin root")
        return None
    return target


def validate_manifest(root: Path, client: str, path: Path,
                      errors: list[str]) -> tuple[dict, str | None]:
    manifest = load_object(path, errors)
    if not manifest:
        return manifest, None
    for field in ("name", "version", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"{client} manifest has no non-empty {field}")
    if manifest.get("name") != PLUGIN_NAME:
        errors.append(f"{client} manifest name must be {PLUGIN_NAME}")
    version = manifest.get("version")
    if isinstance(version, str) and SEMVER_RE.fullmatch(version) is None:
        errors.append(f"{client} manifest version is not strict semver")
    author = manifest.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str):
        errors.append(f"{client} manifest has no author.name")
    skills = relative_path(
        root / PLUGIN_ROOT, manifest.get("skills"),
        f"{client} manifest skills", errors)
    expected = (root / PLUGIN_ROOT / "skills").resolve()
    if skills is not None and skills != expected:
        errors.append(f"{client} manifest must use the canonical skill tree")
    if skills is not None and not skills.is_dir():
        errors.append(f"{client} manifest skill tree does not exist")
    if client == "Codex":
        interface = manifest.get("interface")
        short = interface.get("shortDescription") if isinstance(interface, dict) else None
        prompts = interface.get("defaultPrompt") if isinstance(interface, dict) else None
        if not isinstance(short, str) or not 1 <= len(short) <= 30:
            errors.append("Codex shortDescription must contain 1 to 30 characters")
        if (not isinstance(prompts, list) or not 1 <= len(prompts) <= 3
                or any(not isinstance(prompt, str) or not prompt
                       or len(prompt) > 128 for prompt in prompts)):
            errors.append("Codex defaultPrompt must contain 1 to 3 bounded prompts")
    return manifest, version if isinstance(version, str) else None


def validate_skill_payload(root: Path, errors: list[str]) -> list[str]:
    skills_root = root / PLUGIN_ROOT / "skills"
    if not skills_root.is_dir():
        errors.append("canonical skill tree is missing")
        return []
    skills = sorted(
        path.name for path in skills_root.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file())
    if not skills:
        errors.append("plugin has no discoverable skills")
    unexpected = sorted(
        path.name for path in skills_root.iterdir()
        if path.is_dir() and not (path / "SKILL.md").is_file())
    if unexpected:
        errors.append(
            "plugin skill tree contains directories without SKILL.md: "
            + ", ".join(unexpected))
    return skills


def validate_payload_files(root: Path, errors: list[str]) -> None:
    plugin_root = root / PLUGIN_ROOT
    for directory, names, files in os.walk(plugin_root, followlinks=False):
        parent = Path(directory)
        for name in [*names, *files]:
            path = parent / name
            mode = path.lstat().st_mode
            relative = path.relative_to(plugin_root)
            if stat.S_ISLNK(mode):
                errors.append(f"plugin payload contains a symlink: {relative}")
            elif name in names and not stat.S_ISDIR(mode):
                errors.append(f"plugin payload contains a special directory: {relative}")
            elif name in files and not stat.S_ISREG(mode):
                errors.append(f"plugin payload contains a special file: {relative}")


def validate_codex_marketplace(root: Path, errors: list[str]) -> dict:
    path = root / ".agents/plugins/marketplace.json"
    marketplace = load_object(path, errors)
    if not marketplace:
        return marketplace
    if marketplace.get("name") != MARKETPLACE_NAME:
        errors.append(f"Codex marketplace name must be {MARKETPLACE_NAME}")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        errors.append("Codex marketplace must contain exactly one plugin")
        return marketplace
    entry = plugins[0]
    if not isinstance(entry, dict) or entry.get("name") != PLUGIN_NAME:
        errors.append(f"Codex marketplace plugin must be {PLUGIN_NAME}")
        return marketplace
    source = entry.get("source")
    if source != {"source": "local", "path": "./.agents"}:
        errors.append("Codex marketplace must use the canonical local plugin root")
    if entry.get("policy") != {
            "installation": "AVAILABLE", "authentication": "ON_INSTALL"}:
        errors.append("Codex marketplace policy is incomplete or unsupported")
    if not isinstance(entry.get("category"), str) or not entry["category"]:
        errors.append("Codex marketplace category is missing")
    return marketplace


def validate_claude_marketplace(root: Path, errors: list[str]) -> dict:
    path = root / ".claude-plugin/marketplace.json"
    marketplace = load_object(path, errors)
    if not marketplace:
        return marketplace
    if marketplace.get("name") != MARKETPLACE_NAME:
        errors.append(f"Claude marketplace name must be {MARKETPLACE_NAME}")
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or len(plugins) != 1:
        errors.append("Claude marketplace must contain exactly one plugin")
        return marketplace
    entry = plugins[0]
    if not isinstance(entry, dict) or entry.get("name") != PLUGIN_NAME:
        errors.append(f"Claude marketplace plugin must be {PLUGIN_NAME}")
        return marketplace
    if entry.get("source") != "./.agents":
        errors.append("Claude marketplace must use the canonical local plugin root")
    if "version" in entry:
        errors.append(
            "Claude marketplace must defer version identity to the plugin manifest")
    return marketplace


def plugin_report(root: Path = ROOT) -> dict[str, object]:
    root = root.resolve()
    errors: list[str] = []
    validate_payload_files(root, errors)
    skills = validate_skill_payload(root, errors)
    codex, codex_version = validate_manifest(
        root, "Codex", root / ".agents/.codex-plugin/plugin.json", errors)
    claude, claude_version = validate_manifest(
        root, "Claude", root / ".agents/.claude-plugin/plugin.json", errors)
    validate_codex_marketplace(root, errors)
    validate_claude_marketplace(root, errors)
    versions = {
        "codex": codex_version,
        "claude": claude_version,
    }
    if None not in versions.values() and len(set(versions.values())) != 1:
        errors.append("Codex and Claude plugin versions do not match")
    for client, manifest in (("Codex", codex), ("Claude", claude)):
        if "license" in manifest:
            errors.append(
                f"{client} manifest must not infer a repository license")
    return {
        "schema_version": 1,
        "ok": not errors,
        "plugin": PLUGIN_NAME,
        "plugin_root": str(PLUGIN_ROOT),
        "marketplace": MARKETPLACE_NAME,
        "versions": versions,
        "skills": skills,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=ROOT,
        help="repository root containing the plugin package")
    return parser.parse_args()


def main() -> int:
    report = plugin_report(parse_args().root)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
