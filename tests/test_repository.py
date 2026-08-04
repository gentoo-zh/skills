import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_validator():
    path = ROOT / "scripts" / "validate_repository.py"
    spec = importlib.util.spec_from_file_location("repository_validator_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_repository_validator():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "validate_repository.py")],
        cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "validated 4 skills" in proc.stdout


def test_language_boundary_rejects_non_latin_scripts():
    validator = load_validator()
    assert validator.contains_non_latin_script("English text") is False
    assert validator.contains_non_latin_script("Greek " + chr(0x03B1)) is True
    assert validator.contains_non_latin_script("Cyrillic " + chr(0x0434)) is True


def test_source_authorities_are_limited_to_reviewed_scopes():
    registry = json.loads((
        ROOT / ".agents/skills/gentoo-overlay-development/references/sources.json"
    ).read_text())
    allowlist = registry["authority_scopes"]
    assert all(
        source["scope"] in allowlist[source["authority"]]
        for source in registry["sources"])
    gleps = {source["id"]: source for source in registry["sources"]
             if source["id"] in {"glep-66", "glep-76"}}
    assert {source["scope"] for source in gleps.values()} == {
        "comparative-evidence"}


def test_capability_source_coverage_uses_registered_sources():
    registry = json.loads((
        ROOT / ".agents/skills/gentoo-overlay-development/references/sources.json"
    ).read_text())
    source_ids = {source["id"] for source in registry["sources"]}

    assert registry["capability_sources"]
    assert all(
        sources and set(sources) <= source_ids
        for sources in registry["capability_sources"].values())


def test_skills_stay_within_progressive_disclosure_limits():
    for skill in (ROOT / ".agents" / "skills").iterdir():
        if not skill.is_dir():
            continue
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert len(text.splitlines()) <= 500
        for reference in (skill / "references").glob("*.md"):
            assert f"references/{reference.name}" in text
            reference_text = reference.read_text(encoding="utf-8")
            if len(reference_text.splitlines()) > 100:
                assert "\n## Contents\n" in reference_text
