from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = ROOT / "scripts" / "eval_runner.py"


def load_runner():
    spec = importlib.util.spec_from_file_location("eval_runner_test", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


runner = load_runner()


def case_data():
    cases = [{
        "id": f"case-{number}",
        "should_trigger": number != 6,
        "prompt": f"Prompt {number}",
        "expected": ["review behavior"],
    } for number in range(1, 7)]
    cases[0]["expected_skills"] = ["example-skill"]
    cases[0]["publication_boundary"] = "stop-before-publish"
    return {"schema": 1, "skill": "example-skill", "cases": cases}


def test_static_validation_is_deterministic():
    data = case_data()

    assert runner.validate_case_data(data, "fixture") == []
    data["cases"][1]["expected_skills"] = ["bad name"]
    assert runner.validate_case_data(data, "fixture") == [
        "fixture: case-2 has invalid expected_skills"]


def test_template_requires_prompt_and_rejects_unknown_fields():
    with pytest.raises(Exception, match="must contain the .*prompt.* field"):
        runner.parse_command_template('["client", "{skill_name}"]')
    with pytest.raises(Exception, match="unknown command template fields"):
        runner.parse_command_template('["client", "{prompt}", "{expected}"]')


def test_external_case_does_not_pass_expected_answers(tmp_path):
    expected_secret = "never pass this expected answer"
    case = case_data()["cases"][0]
    case["expected"] = [expected_secret]
    skill = {"name": "example-skill", "path": str(tmp_path)}
    expected_hash = hashlib.sha256(expected_secret.encode()).hexdigest()
    code = (
        "import hashlib,json,sys; prompt=sys.argv[1]; "
        "print(json.dumps({'triggered': True, "
        "'selected_skills': ['example-skill'], "
        "'publication_state': 'stopped', "
        "'saw_expected': hashlib.sha256(prompt.encode()).hexdigest() == %r}))"
        % expected_hash)
    template = [sys.executable, "-c", code, "{prompt}"]

    result = runner.run_case(template, case, skill, tmp_path, 5, 32768)

    assert result["ok"] is True
    assert result["protocol"] == {
        "triggered": True,
        "selected_skills": ["example-skill"],
        "publication_state": "stopped",
    }
    assert expected_secret not in " ".join(result["command"])
    assert case["prompt"] not in " ".join(result["command"])


def test_output_limit_is_fail_closed(tmp_path):
    result = runner.run_bounded(
        [sys.executable, "-c", "print('x' * 10000)"], tmp_path, 5, 100)

    assert result["output_truncated"] is True
    assert result["stdout_bytes"] + result["stderr_bytes"] == 100


def test_skill_snapshot_excludes_eval_answers(tmp_path):
    skill = tmp_path / "example-skill"
    (skill / "evals").mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill body\n", encoding="utf-8")
    (skill / "evals" / "cases.json").write_text(
        "secret expectation\n", encoding="utf-8")
    destination = tmp_path / "isolated"
    destination.mkdir()

    snapshot = runner.prepare_skill_snapshot(skill, destination)

    assert (snapshot / "SKILL.md").read_text() == "skill body\n"
    assert not (snapshot / "evals").exists()


def test_command_template_cannot_contain_expectations():
    case = case_data()["cases"][0]

    with pytest.raises(runner.EvalError, match="contains eval expectation"):
        runner.reject_expectation_leak(
            ["client", "review behavior", "{prompt}"], [case])


def test_static_cli_report(tmp_path):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps(case_data()), encoding="utf-8")
    output = tmp_path / "report.json"

    status = runner.main(["static", str(cases_path), "--output", str(output)])
    report = json.loads(output.read_text(encoding="utf-8"))

    assert status == 0
    assert report["mode"] == "static"
    assert report["complete"] is True
    assert report["ok"] is True
