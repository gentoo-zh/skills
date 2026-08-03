from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT / ".agents" / "skills" / "gentoo-overlay-development"
          / "scripts" / "dependency_analyzer.py")


def load_module():
    spec = importlib.util.spec_from_file_location("dependency_analyzer_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


analyzer = load_module()


def run_script(*arguments: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments], cwd=ROOT,
        capture_output=True, text=True)


def test_reduces_explicit_use_and_preserves_provenance():
    report = analyzer.analyze({
        "eapi": "8",
        "use": {"enabled": ["ssl"], "disabled": ["test"]},
        "fields": {
            "DEPEND": "ssl? ( dev-libs/openssl:= ) !ssl? ( dev-libs/libressl )",
            "RDEPEND": "dev-libs/openssl:= !app-crypt/oldssl",
            "BDEPEND": "test? ( dev-util/cmake ) dev-build/ninja",
            "IDEPEND": "sys-apps/cache-helper",
            "PDEPEND": "app-misc/post-helper",
        },
    })

    assert report["schema_version"] == 1
    assert report["selection"] == "reduced"
    assert report["fields"]["DEPEND"]["reduced"] == ["dev-libs/openssl:="]
    assert report["fields"]["BDEPEND"]["reduced"] == ["dev-build/ninja"]
    assert report["use"] == {
        "provided": True,
        "enabled": ["ssl"],
        "disabled": ["test"],
        "referenced": ["ssl", "test"],
    }
    duplicate = report["cross_field_duplicates"][0]
    assert duplicate["atom"] == "dev-libs/openssl:="
    assert [item["field"] for item in duplicate["provenance"]] == [
        "DEPEND", "RDEPEND"]
    assert report["blockers"] == [{
        "atom": "!app-crypt/oldssl",
        "strength": "weak",
        "provenance": {
            "field": "RDEPEND", "occurrence": 1,
            "source": "fields.RDEPEND",
        },
    }]
    assert [item["atom"] for item in report["slot_operators"]] == [
        "dev-libs/openssl:=", "dev-libs/openssl:="]
    assert report["repository_qualifiers"] == []


def test_without_use_state_lists_potential_branches_without_reducing():
    report = analyzer.analyze({
        "eapi": "8",
        "DEPEND": "gui? ( x11-libs/gtk+ ) !gui? ( dev-libs/ncurses )",
    })

    assert report["selection"] == "potential"
    assert report["fields"]["DEPEND"]["atoms"] == [
        "x11-libs/gtk+", "dev-libs/ncurses"]
    assert report["fields"]["DEPEND"]["reduced"] is None
    assert report["use"]["referenced"] == ["gui"]
    assert "every conditional branch" in report["limitations"][-1]


def test_json_input_and_explicit_fields_are_deterministic(tmp_path):
    input_path = tmp_path / "dependencies.json"
    input_path.write_text(json.dumps({
        "eapi": 8,
        "use": ["python", "-test"],
        "dependencies": {
            "DEPEND": "python? ( dev-lang/python:3.13 )",
            "BDEPEND": "test? ( dev-python/pytest )",
        },
    }))

    output_path = tmp_path / "analysis.json"
    from_json = run_script(
        "--input", str(input_path), "--output", str(output_path))
    explicit = run_script(
        "--eapi", "8", "--use", "python", "--use=-test",
        "--depend", "python? ( dev-lang/python:3.13 )",
        "--bdepend", "test? ( dev-python/pytest )")
    repeated = run_script("--input", str(input_path))

    assert from_json.returncode == explicit.returncode == repeated.returncode == 0
    report = json.loads(output_path.read_text())
    repeated_report = json.loads(repeated.stdout)
    generated_at = report.pop("generated_at")
    repeated_report.pop("generated_at")
    assert report == repeated_report
    assert generated_at.endswith("Z")
    assert from_json.stdout == (
        f"Wrote complete dependency report to {output_path}.\n")
    assert report["complete"] is True
    assert report["truncated"] is False
    assert report["input_provenance"] == {
        "bytes": len(input_path.read_bytes()),
        "kind": "json-file",
        "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "source": str(input_path),
    }
    assert report["fields"]["DEPEND"]["atoms"] == [
        "dev-lang/python:3.13"]
    assert json.loads(explicit.stdout)["fields"]["BDEPEND"]["atoms"] == []


def test_input_size_is_bounded_before_json_parsing(tmp_path):
    input_path = tmp_path / "oversized.json"
    input_path.write_bytes(b" " * (analyzer.MAX_INPUT_BYTES + 1))

    proc = run_script("--input", str(input_path))
    report = json.loads(proc.stdout)

    assert proc.returncode == 2
    assert report["error"]["code"] == "input_too_large"
    assert report["complete"] is True


@pytest.mark.parametrize(
    ("document", "code"),
    [
        ({"eapi": "999", "DEPEND": "dev-libs/a"}, "unsupported_eapi"),
        ({"eapi": "6", "BDEPEND": "dev-build/cmake"}, "unsupported_field"),
        ({"eapi": "7", "IDEPEND": "sys-apps/a"}, "unsupported_field"),
        ({"eapi": "8", "DEPEND": "foo? ( dev-libs/a"}, "invalid_dependency"),
        ({"eapi": "8", "DEPEND": "dev-libs/a::gentoo"}, "invalid_dependency"),
        ({"eapi": "8", "PDEPEND": "dev-libs/a:="}, "invalid_dependency"),
        ({"eapi": "8", "DEPEND": "dev-libs/a:1/2="}, "invalid_dependency"),
        ({
            "eapi": "8", "use": {"enabled": [], "disabled": []},
            "DEPEND": "foo? ( dev-libs/a )",
        }, "incomplete_use_state"),
    ],
)
def test_invalid_or_unsupported_metadata_fails_closed(document, code):
    with pytest.raises(analyzer.AnalysisError) as excinfo:
        analyzer.analyze(document)
    assert excinfo.value.code == code


@pytest.mark.parametrize("flag", ["-foo", "foo/bar", "foo:bar", "foo."])
def test_invalid_conditional_flags_use_portage_validation(flag):
    with pytest.raises(analyzer.AnalysisError) as excinfo:
        analyzer.analyze({
            "eapi": "8",
            "DEPEND": f"{flag}? ( dev-libs/a )",
        })

    assert excinfo.value.code == "invalid_dependency"


def test_missing_portage_fails_with_versioned_json():
    proc = subprocess.run(
        [sys.executable, "-S", str(SCRIPT), "--eapi", "8",
         "--depend", "dev-libs/a"],
        cwd=ROOT, capture_output=True, text=True)

    report = json.loads(proc.stdout)
    assert proc.returncode == 2
    assert proc.stderr == ""
    assert report == {
        "complete": True,
        "generated_at": report["generated_at"],
        "input_provenance": {
            "bytes": len(b'{"DEPEND":"dev-libs/a","eapi":"8"}'),
            "kind": "explicit-fields",
            "sha256": hashlib.sha256(
                b'{"DEPEND":"dev-libs/a","eapi":"8"}').hexdigest(),
            "source": "command-line",
        },
        "schema_version": 1,
        "tool": "gentoo-overlay-dependency-analyzer",
        "ok": False,
        "truncated": False,
        "error": {
            "code": "portage_unavailable",
            "message": "the Portage Python API is required for dependency analysis",
        },
    }


def test_equals_slot_operator_inside_any_of_fails_closed():
    with pytest.raises(analyzer.AnalysisError, match="any-of"):
        analyzer.analyze({
            "eapi": "8",
            "DEPEND": "|| ( dev-libs/a:= dev-libs/b )",
        })


def test_legacy_single_bang_blocker_strength_is_not_inferred():
    report = analyzer.analyze({"eapi": "1", "DEPEND": "!dev-libs/a"})

    assert report["blockers"][0]["strength"] == "unspecified"
