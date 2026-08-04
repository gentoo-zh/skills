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


def test_chinese_commit_and_pr_guidance_is_meaning_first():
    skill_root = ROOT / ".agents/skills/gzh-version-bump"
    finish = (skill_root / "references/finish-pipeline.md").read_text()
    publishing = (skill_root / "references/publishing.md").read_text()
    normalized_finish = " ".join(finish.split())
    normalized_publishing = " ".join(publishing.split())
    example = load_validator().CHINESE_PR_EXAMPLE_RE.search(finish)

    assert example is not None
    assert "new upstream binary package" in normalized_finish
    assert "Never translate source wording word by word" in normalized_finish
    assert "Carry only verified cause and effect" in normalized_finish
    assert "Reuse only the verified causal" in normalized_publishing
    assert "instead of translating word by word or" in normalized_publishing

    natural_term = "\u9884\u7f16\u8bd1\u5305"
    literal_coinage = "\u65b0\u5236\u54c1"
    assert natural_term in example.group(0)
    assert literal_coinage not in example.group(0)

    version_cases = json.loads((
        skill_root / "evals/cases.json").read_text())["cases"]
    generic_cases = json.loads((
        ROOT / ".agents/skills/gentoo-overlay-development/evals/cases.json"
    ).read_text())["cases"]
    regressions = {
        case["id"]: case
        for case in version_cases + generic_cases
        if case["id"] in {
            "chinese-pr-meaning-first",
            "gentoo-zh-new-package-chinese-pr-wording",
        }
    }

    assert set(regressions) == {
        "chinese-pr-meaning-first",
        "gentoo-zh-new-package-chinese-pr-wording",
    }
    for case in regressions.values():
        expectations = " ".join(case["expected"])
        assert "exact pkgdev-generated English subject unchanged" in expectations
        assert "do not translate" in expectations
        assert "verified causal rationale" in expectations


def test_change_surface_routing_keeps_gentoo_hard_gates():
    version_root = ROOT / ".agents/skills/gzh-version-bump"
    version_skill = " ".join((version_root / "SKILL.md").read_text().split())
    finish = " ".join((
        version_root / "references/finish-pipeline.md").read_text().split())
    version_cases = {
        case["id"]: case for case in json.loads((
            version_root / "evals/cases.json"
        ).read_text())["cases"]
    }
    generic_cases = {
        case["id"]: case for case in json.loads((
            ROOT / ".agents/skills/gentoo-overlay-development/evals/cases.json"
        ).read_text())["cases"]
    }

    copy_bump = " ".join(version_cases["verified-source-copy-bump"]["expected"])
    prebuilt = " ".join(
        version_cases["prebuilt-artifact-layout-change"]["expected"])
    qa_exclusion = version_cases["qa-only-fix-exclusion"]
    qa_route = " ".join(generic_cases["gentoo-zh-qa-fix-route"]["expected"])

    assert "manual Gentoo semantic" in copy_bump
    assert "every live hard gate" in copy_bump
    assert "do not load dependency prebuilt binary image or test-matrix" in copy_bump
    assert "authorized named executor exists" in copy_bump
    assert "artifact static binary and strict installed-image" in prebuilt
    assert qa_exclusion["should_trigger"] is False
    assert qa_exclusion["expected_skills"] == ["gentoo-overlay-development"]
    assert "every live hard gate" in qa_route
    assert "artifact selection and topology" in version_skill
    assert "Executor guidance remains conditional on the environment" in version_skill
    assert "authorized named executor" in finish


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
    source_scopes = {
        source["id"]: source["scope"] for source in registry["sources"]}
    assert set(registry["capability_scopes"]) == set(
        registry["capability_sources"])
    assert all(
        set(registry["capability_scopes"][capability]) == {
            source_scopes[source_id] for source_id in sources}
        for capability, sources in registry["capability_sources"].items())


def test_skills_stay_within_progressive_disclosure_limits():
    validator = load_validator()
    records = []
    for skill in (ROOT / ".agents" / "skills").iterdir():
        if not skill.is_dir():
            continue
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        fields = validator.frontmatter(skill / "SKILL.md", [])
        records.append(validator.initial_skill_record(skill, fields))
        assert len(text.splitlines()) <= 500
        for reference in (skill / "references").glob("*.md"):
            assert f"references/{reference.name}" in text
            reference_text = reference.read_text(encoding="utf-8")
            if len(reference_text.splitlines()) > 100:
                assert "\n## Contents\n" in reference_text
    assert len(validator.serialize_initial_skill_list(records)) <= (
        validator.MAX_INITIAL_SKILL_LIST_CHARACTERS)


def test_nested_references_are_rejected_even_when_directly_linked(tmp_path):
    validator = load_validator()
    validator.ROOT = tmp_path
    skill = tmp_path / "skills/example"
    nested = skill / "references/topic/details.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("# Details\n", encoding="utf-8")

    errors = []
    validator.validate_references(
        skill, "[details](references/topic/details.md)\n", errors)

    assert errors == [
        "reference must be one level deep: "
        "skills/example/references/topic/details.md"
    ]


def test_initial_skill_list_serialization_is_exact_and_sorted(tmp_path):
    validator = load_validator()
    alpha = tmp_path / "skills/alpha"
    zeta = tmp_path / "skills/zeta"
    records = [
        validator.initial_skill_record(
            zeta, {"name": "zeta", "description": "Last skill"}),
        validator.initial_skill_record(
            alpha, {"name": "alpha", "description": "First skill"}),
    ]

    serialized = validator.serialize_initial_skill_list(records)

    expected = (
        '[{"name":"alpha","description":"First skill","path":"'
        f'{(alpha / "SKILL.md").resolve()}'
        '"},{"name":"zeta","description":"Last skill","path":"'
        f'{(zeta / "SKILL.md").resolve()}"}}]'
    )
    assert serialized == expected
    assert len(serialized) == len(expected)


def test_initial_skill_list_ceiling_is_exact():
    validator = load_validator()
    record = {"name": "skill", "description": "", "path": "/skill/SKILL.md"}
    base = len(validator.serialize_initial_skill_list([record]))
    record["description"] = "x" * (
        validator.MAX_INITIAL_SKILL_LIST_CHARACTERS - base)

    errors = []
    assert validator.validate_initial_skill_list([record], errors) == 8000
    assert errors == []

    record["description"] += "x"
    assert validator.validate_initial_skill_list([record], errors) == 8001
    assert errors == [
        "serialized initial skill-list estimate exceeds the ceiling: 8001 > 8000"
    ]


def test_local_markdown_links_stay_within_the_owning_skill(tmp_path):
    validator = load_validator()
    validator.ROOT = tmp_path
    validator.SKILLS_ROOT = tmp_path / "skills"
    first = validator.SKILLS_ROOT / "first"
    second = validator.SKILLS_ROOT / "second"
    (first / "references").mkdir(parents=True)
    second.mkdir(parents=True)
    (first / "SKILL.md").write_text(
        "[details](references/details.md)\n", encoding="utf-8")
    (first / "references/details.md").write_text(
        "[other](../../second/SKILL.md)\n", encoding="utf-8")
    (second / "SKILL.md").write_text("# Other\n", encoding="utf-8")

    errors = []
    validator.validate_links(errors)

    assert errors == [
        "local link escapes skill root in "
        "skills/first/references/details.md: ../../second/SKILL.md"
    ]


def test_local_markdown_links_report_missing_targets(tmp_path):
    validator = load_validator()
    validator.ROOT = tmp_path
    validator.SKILLS_ROOT = tmp_path / "skills"
    skill = validator.SKILLS_ROOT / "example"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "[missing](references/missing.md)\n", encoding="utf-8")

    errors = []
    validator.validate_links(errors)

    assert errors == [
        "broken local link in skills/example/SKILL.md: references/missing.md"
    ]
