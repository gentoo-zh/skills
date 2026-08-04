#!/usr/bin/env python3
"""Validate skill packaging, metadata, evals, links, and evidence records."""

from __future__ import annotations

import importlib.util
import json
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = ROOT / ".agents" / "skills"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REVISION_RE = re.compile(r"[0-9a-f]{40}")
MEDIAWIKI_REVISION_RE = re.compile(r"[1-9][0-9]*")
UTC_TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
LOCAL_LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")
CHINESE_PR_EXAMPLE_RE = re.compile(
    r"Chinese PR body example:\s*\n```(?:text|markdown)\n.*?```", re.DOTALL)
MAX_SKILL_LINES = 500
MAX_REFERENCE_LINES_WITHOUT_CONTENTS = 100
CONTENTS_HEADING_RE = re.compile(r"^## Contents\s*$", re.MULTILINE)


def load_eval_validator():
    path = ROOT / "scripts" / "eval_runner.py"
    spec = importlib.util.spec_from_file_location(
        "gentoo_skill_eval_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load deterministic eval validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_case_data


validate_case_data = load_eval_validator()


def load_json(path: Path, errors: list[str]) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"invalid JSON {path.relative_to(ROOT)}: {exc}")
        return {}


def frontmatter(path: Path, errors: list[str]) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        errors.append(f"invalid frontmatter: {path.relative_to(ROOT)}")
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if not separator or not value.strip():
            errors.append(f"malformed frontmatter line in {path.relative_to(ROOT)}")
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        fields[key.strip()] = value
    if list(fields) != ["name", "description"]:
        errors.append(
            f"frontmatter must contain only name and description: {path.relative_to(ROOT)}")
    return fields


def validate_skill(skill: Path, errors: list[str]) -> None:
    skill_md = skill / "SKILL.md"
    if not skill_md.is_file():
        errors.append(f"missing SKILL.md: {skill.relative_to(ROOT)}")
        return
    fields = frontmatter(skill_md, errors)
    skill_text = skill_md.read_text(encoding="utf-8")
    if len(skill_text.splitlines()) > MAX_SKILL_LINES:
        errors.append(
            f"SKILL.md exceeds {MAX_SKILL_LINES} lines: {skill.relative_to(ROOT)}")
    name = fields.get("name", "")
    description = fields.get("description", "")
    if name != skill.name or not NAME_RE.fullmatch(name) or len(name) > 64:
        errors.append(f"invalid skill name: {skill.relative_to(ROOT)}")
    if not description or len(description) > 1024 or "<" in description or ">" in description:
        errors.append(f"invalid skill description: {skill.relative_to(ROOT)}")

    metadata = skill / "agents" / "openai.yaml"
    if not metadata.is_file():
        errors.append(f"missing agents/openai.yaml: {skill.relative_to(ROOT)}")
    else:
        text = metadata.read_text(encoding="utf-8")
        for field in ("display_name", "short_description", "default_prompt"):
            match = re.search(rf'^\s{{2}}{field}:\s+"([^"]+)"\s*$', text, re.M)
            if not match:
                errors.append(f"missing quoted {field}: {metadata.relative_to(ROOT)}")
            elif field == "short_description" and not 25 <= len(match.group(1)) <= 64:
                errors.append(f"short_description length is invalid: {metadata.relative_to(ROOT)}")
            elif field == "default_prompt" and f"${name}" not in match.group(1):
                errors.append(f"default_prompt does not name ${name}: {metadata.relative_to(ROOT)}")

    eval_path = skill / "evals" / "cases.json"
    data = load_json(eval_path, errors)
    errors.extend(validate_case_data(
        data, str(eval_path.relative_to(ROOT)), expected_skill=name))

    references = skill / "references"
    if references.is_dir():
        for reference in references.glob("*.md"):
            target = f"references/{reference.name}"
            if target not in skill_text:
                errors.append(
                    "reference is not directly discoverable from SKILL.md: "
                    f"{reference.relative_to(ROOT)}")
            reference_text = reference.read_text(encoding="utf-8")
            if (len(reference_text.splitlines()) > MAX_REFERENCE_LINES_WITHOUT_CONTENTS
                    and not CONTENTS_HEADING_RE.search(reference_text)):
                errors.append(
                    "long reference has no Contents section: "
                    f"{reference.relative_to(ROOT)}")


def validate_sources(errors: list[str]) -> None:
    references = SKILLS_ROOT / "gentoo-overlay-development" / "references"
    registry = load_json(references / "sources.json", errors)
    lock = load_json(references / "source-lock.json", errors)
    sources = registry.get("sources", [])
    if registry.get("schema") != 1 or len(sources) < 20:
        errors.append("source registry is missing or too small")
        return
    authorities = set(registry.get("authorities", []))
    scopes = set(registry.get("scopes", []))
    authority_scopes = registry.get("authority_scopes")
    if (not isinstance(authority_scopes, dict)
            or set(authority_scopes) != authorities
            or any(not isinstance(values, list) or not values
                   or any(value not in scopes for value in values)
                   for values in authority_scopes.values())):
        errors.append("source authority scope allowlist is invalid")
        authority_scopes = {}
    ids = [source.get("id") for source in sources]
    if len(ids) != len(set(ids)):
        errors.append("source ids must be unique")
    capability_sources = registry.get("capability_sources")
    if not isinstance(capability_sources, dict) or not capability_sources:
        errors.append("source capability coverage is missing")
    else:
        for capability, source_ids in capability_sources.items():
            if (not NAME_RE.fullmatch(capability)
                    or not isinstance(source_ids, list) or not source_ids
                    or len(source_ids) != len(set(source_ids))
                    or any(source_id not in ids for source_id in source_ids)):
                errors.append(
                    f"invalid source capability coverage: {capability}")
    for source in sources:
        required = {
            "id", "title", "authority", "scope", "kind", "url", "topics", "use"}
        if not required.issubset(source):
            errors.append(f"incomplete source: {source.get('id')}")
            continue
        if source["authority"] not in authorities:
            errors.append(f"unknown source authority: {source['id']}")
        if source["scope"] not in scopes:
            errors.append(f"unknown source scope: {source['id']}")
        elif (source["authority"] in authority_scopes
              and source["scope"] not in authority_scopes[source["authority"]]):
            errors.append(
                f"source authority is not allowed in scope: {source['id']}")
        if source["kind"] not in {"git", "http", "mediawiki"}:
            errors.append(f"invalid source kind: {source['id']}")
        if source["kind"] == "mediawiki" and not source.get("api_url", "").startswith(
                "https://"):
            errors.append(f"mediawiki source has no HTTPS API: {source['id']}")
        if not source["url"].startswith("https://"):
            errors.append(f"source is not HTTPS: {source['id']}")
    locked = lock.get("sources", {})
    if lock.get("schema") != 1 or set(ids) != set(locked):
        errors.append("source lock ids do not match the registry")
        return
    kinds = {source["id"]: source["kind"] for source in sources}
    for identifier, record in locked.items():
        kind = kinds[identifier]
        key = "sha256" if kind == "http" else "revision"
        pattern = (REVISION_RE if kind == "git" else
                   MEDIAWIKI_REVISION_RE if kind == "mediawiki" else SHA256_RE)
        if not pattern.fullmatch(record.get(key, "")):
            errors.append(f"invalid locked {key}: {identifier}")
        if not UTC_TIMESTAMP_RE.fullmatch(record.get("checked_at", "")):
            errors.append(f"source lock has no UTC check timestamp: {identifier}")
    if not UTC_TIMESTAMP_RE.fullmatch(lock.get("updated_at", "")):
        errors.append("source lock has no UTC update timestamp")


def validate_links(errors: list[str]) -> None:
    for path in SKILLS_ROOT.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for raw_target in LOCAL_LINK_RE.findall(text):
            target = raw_target.strip("<>").split("#", 1)[0]
            if not target or "://" in target or target.startswith("mailto:"):
                continue
            if not (path.parent / target).resolve().exists():
                errors.append(
                    f"broken local link in {path.relative_to(ROOT)}: {raw_target}")


def contains_non_latin_script(text: str) -> bool:
    for character in text:
        if not unicodedata.category(character).startswith("L"):
            continue
        if "LATIN" not in unicodedata.name(character, ""):
            return True
    return False


def validate_english_skill_content(errors: list[str]) -> None:
    text_suffixes = {".md", ".json", ".yaml", ".yml", ".py"}
    for path in SKILLS_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            text = CHINESE_PR_EXAMPLE_RE.sub("", text)
        if contains_non_latin_script(text):
            errors.append(f"skill content must be English: {path.relative_to(ROOT)}")


def validate_english_code(errors: list[str]) -> None:
    for directory in (ROOT / "gzh", ROOT / "scripts", ROOT / "tests", ROOT / ".github"):
        for path in directory.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".sh", ".yaml", ".yml"}:
                continue
            if contains_non_latin_script(path.read_text(encoding="utf-8")):
                errors.append(f"code and comments must be English: {path.relative_to(ROOT)}")


def validate_executables(errors: list[str]) -> None:
    paths = [ROOT / "install.sh", ROOT / "update.sh",
             ROOT / "scripts" / "eval_runner.py"]
    paths.extend(SKILLS_ROOT.glob("*/scripts/*.py"))
    paths.extend([ROOT / "scripts" / "install.py", ROOT / "scripts" / "update.py"])
    for path in paths:
        if not path.is_file() or not path.stat().st_mode & stat.S_IXUSR:
            errors.append(f"script is missing or not executable: {path.relative_to(ROOT)}")


def validate_tracked_files(errors: list[str]) -> None:
    proc = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True)
    unwanted = [path for path in proc.stdout.splitlines()
                if "__pycache__" in path or path.endswith((".pyc", ".pyo"))]
    if unwanted:
        errors.append(f"generated Python files are tracked: {', '.join(unwanted)}")


def main() -> int:
    errors: list[str] = []
    skills = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
    if not skills:
        errors.append("no skills found")
    for skill in skills:
        validate_skill(skill, errors)
    validate_sources(errors)
    validate_links(errors)
    validate_english_skill_content(errors)
    validate_english_code(errors)
    validate_executables(errors)
    validate_tracked_files(errors)
    if errors:
        print("repository validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        f"validated {len(skills)} skills, "
        f"{len(load_json(SKILLS_ROOT / 'gentoo-overlay-development/references/sources.json', [])['sources'])} sources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
