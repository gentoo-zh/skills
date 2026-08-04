#!/usr/bin/env python3
"""Validate the repository release identity and artifact boundary."""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "gzh" / "pyproject.toml"
PACKAGE_INIT = ROOT / "gzh" / "gzh" / "__init__.py"
RELEASE_CONTRACT = ROOT / "RELEASING.md"
PLUGIN_MANIFESTS = {
    "codex_plugin": Path(".agents/.codex-plugin/plugin.json"),
    "claude_plugin": Path(".agents/.claude-plugin/plugin.json"),
}
LICENSE_NAMES = (
    "COPYING", "COPYING.md", "COPYING.rst", "COPYING.txt",
    "LICENSE", "LICENSE.md", "LICENSE.rst", "LICENSE.txt",
)


def package_version(path: Path) -> tuple[str, dict]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project")
    if not isinstance(project, dict) or not isinstance(project.get("version"), str):
        raise ValueError("gzh pyproject.toml has no string project.version")
    return project["version"], project


def source_version(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values = []
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name)
                        and target.id == "__version__" for target in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            values.append(node.value.value)
    if len(values) != 1:
        raise ValueError("gzh.__version__ must have one literal string assignment")
    return values[0]


def plugin_versions(root: Path) -> dict[str, str]:
    versions = {}
    for name, relative in PLUGIN_MANIFESTS.items():
        data = json.loads((root / relative).read_text(encoding="utf-8"))
        version = data.get("version") if isinstance(data, dict) else None
        if not isinstance(version, str):
            raise ValueError(f"{relative} has no string version")
        versions[name] = version
    return versions


def cli_version(root: Path) -> str:
    environment = os.environ.copy()
    package_root = str(root / "gzh")
    environment["PYTHONPATH"] = (
        package_root if not environment.get("PYTHONPATH")
        else package_root + os.pathsep + environment["PYTHONPATH"])
    result = subprocess.run(
        [sys.executable, "-m", "gzh.cli", "--version"],
        cwd=root, env=environment, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        raise ValueError(
            "gzh --version failed: " + (result.stderr.strip() or result.stdout.strip()))
    match = re.search(r"\bversion\s+(\S+)\s*$", result.stdout)
    if match is None:
        raise ValueError("gzh --version returned an unrecognized value")
    return match.group(1)


def git_value(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments], cwd=root, check=False,
        capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ValueError(detail)
    return result.stdout.strip()


def annotated_tag_identity(root: Path, tag: str) -> dict[str, str]:
    reference = f"refs/tags/{tag}"
    try:
        object_type = git_value(root, "cat-file", "-t", reference)
        target = git_value(root, "rev-parse", f"{reference}^{{commit}}")
        head = git_value(root, "rev-parse", "HEAD")
    except ValueError as exc:
        raise ValueError(f"cannot verify release tag {tag}: {exc}") from exc
    return {
        "name": tag,
        "object_type": object_type,
        "target": target,
        "head": head,
    }


def release_report(
    *,
    root: Path = ROOT,
    tag: str | None,
    mode: str,
    observed_cli_version: str | None = None,
    require_annotated_tag: bool = False,
) -> dict[str, object]:
    pyproject_version, project = package_version(root / "gzh" / "pyproject.toml")
    init_version = source_version(root / "gzh" / "gzh" / "__init__.py")
    command_version = observed_cli_version or cli_version(root)
    expected_tag = f"v{pyproject_version}"
    license_files = [name for name in LICENSE_NAMES if (root / name).is_file()]
    metadata_declared = "license" in project or "license-files" in project
    errors = []
    tag_identity = None
    declarations = {
        "pyproject": pyproject_version,
        "package": init_version,
        "cli": command_version,
        **plugin_versions(root),
    }
    if len(set(declarations.values())) != 1:
        errors.append("version declarations do not match")
    if tag is not None and tag != expected_tag:
        errors.append(f"release tag must be {expected_tag}")
    if not (root / "RELEASING.md").is_file():
        errors.append("RELEASING.md is required")
    if require_annotated_tag:
        if tag is None:
            errors.append("annotated tag verification requires an exact release tag")
        else:
            try:
                tag_identity = annotated_tag_identity(root, tag)
            except ValueError as exc:
                errors.append(str(exc))
            else:
                if tag_identity["object_type"] != "tag":
                    errors.append(f"release tag {tag} must be annotated")
                if tag_identity["target"] != tag_identity["head"]:
                    errors.append(f"release tag {tag} does not point to HEAD")
    if mode == "package":
        if not metadata_declared or not license_files:
            errors.append(
                "package artifacts require explicit project license metadata and a root license file")
        errors.append(
            "custom package artifacts remain disabled until a reviewed deterministic rights decision contract is implemented")
    return {
        "schema_version": 1,
        "ok": not errors,
        "mode": mode,
        "version": pyproject_version,
        "expected_tag": expected_tag,
        "requested_tag": tag,
        "tag_verification": tag_identity,
        "version_declarations": declarations,
        "plugin_package": {
            "name": "gentoo-overlay-skills",
            "root": ".agents",
            "marketplace": "gentoo-zh-skills",
        },
        "update_channel": "master",
        "rights": {
            "metadata_declared": metadata_declared,
            "root_license_files": license_files,
            "reviewed_decision": False,
            "status": (
                "declared-unreviewed"
                if metadata_declared and license_files else "undeclared"),
        },
        "custom_package_artifacts_allowed": False,
        "errors": errors,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("source-only", "package"), required=True,
        help="select generated source archives or custom package artifacts")
    parser.add_argument("--tag", help="require the exact release tag")
    parser.add_argument(
        "--require-annotated-tag", action="store_true",
        help="require the exact tag to be annotated and point to HEAD")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = release_report(
            tag=args.tag, mode=args.mode,
            require_annotated_tag=args.require_annotated_tag)
    except (OSError, SyntaxError, ValueError, tomllib.TOMLDecodeError) as exc:
        report = {
            "schema_version": 1,
            "ok": False,
            "mode": args.mode,
            "errors": [f"{type(exc).__name__}: {exc}"],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
