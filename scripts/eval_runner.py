#!/usr/bin/env python3
"""Validate skill eval cases or run bounded, opt-in routing evaluations.

The external command template is a JSON array with ``{prompt}`` and optional
``{skill_path}``, ``{skill_name}``, and ``{case_id}`` fields. The client must write one
JSON object to stdout with ``triggered``, ``selected_skills``, and
``publication_state``. Expected answers are never copied into the command or skill
snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = ROOT / ".agents" / "skills"
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
ALLOWED_FIELDS = {"prompt", "skill_path", "skill_name", "case_id"}
PUBLICATION_BOUNDARIES = {
    "not-requested",
    "stop-before-publish",
    "live-procedure-required",
}
PUBLICATION_STATES = {"not-requested", "stopped", "published"}
DEFAULT_MAX_OUTPUT_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 120.0
MAX_TIMEOUT_SECONDS = 1800.0


class EvalError(RuntimeError):
    """An eval input or bounded execution is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_case_data(data: object, label: str = "cases",
                       expected_skill: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: root must be an object"]
    skill = data.get("skill")
    cases = data.get("cases")
    if data.get("schema") != 1:
        errors.append(f"{label}: unsupported schema")
    if not isinstance(skill, str) or not NAME_RE.fullmatch(skill):
        errors.append(f"{label}: invalid skill name")
    elif expected_skill is not None and skill != expected_skill:
        errors.append(f"{label}: skill does not match {expected_skill}")
    if not isinstance(cases, list) or len(cases) < 6:
        errors.append(f"{label}: at least six cases are required")
        return errors
    identifiers: set[str] = set()
    activations: set[bool] = set()
    for index, case in enumerate(cases):
        prefix = f"{label}: case {index + 1}"
        if not isinstance(case, dict):
            errors.append(f"{prefix} must be an object")
            continue
        identifier = case.get("id")
        if not isinstance(identifier, str) or not NAME_RE.fullmatch(identifier):
            errors.append(f"{prefix} has an invalid id")
        elif identifier in identifiers:
            errors.append(f"{label}: duplicate case id: {identifier}")
        else:
            identifiers.add(identifier)
            prefix = f"{label}: {identifier}"
        prompt = case.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{prefix} has no prompt")
        should_trigger = case.get("should_trigger")
        if not isinstance(should_trigger, bool):
            errors.append(f"{prefix} has no boolean should_trigger")
        else:
            activations.add(should_trigger)
        expected = case.get("expected")
        if (not isinstance(expected, list) or not expected
                or any(not isinstance(item, str) or not item.strip()
                       for item in expected)):
            errors.append(f"{prefix} has invalid review expectations")
        expected_skills = case.get("expected_skills")
        if expected_skills is not None and (
                not isinstance(expected_skills, list)
                or any(not isinstance(item, str) or not NAME_RE.fullmatch(item)
                       for item in expected_skills)
                or len(expected_skills) != len(set(expected_skills))):
            errors.append(f"{prefix} has invalid expected_skills")
        boundary = case.get("publication_boundary")
        if boundary is not None and boundary not in PUBLICATION_BOUNDARIES:
            errors.append(f"{prefix} has an invalid publication_boundary")
    if activations != {True, False}:
        errors.append(f"{label}: activation and exclusion cases are required")
    return errors


def load_cases(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvalError(f"cannot read {path}: {exc}") from exc
    errors = validate_case_data(data, str(path))
    if errors:
        raise EvalError("\n".join(errors))
    return data


def case_paths(values: list[Path]) -> list[Path]:
    if values:
        return [path.resolve() for path in values]
    return sorted(SKILLS_ROOT.glob("*/evals/cases.json"))


def parse_command_template(value: str) -> list[str]:
    try:
        command = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(
            "command template must be a JSON array of strings") from exc
    if (not isinstance(command, list) or not command
            or any(not isinstance(part, str) or not part for part in command)):
        raise argparse.ArgumentTypeError(
            "command template must be a non-empty JSON array of strings")
    fields = {
        match.group(1)
        for part in command
        for match in re.finditer(r"\{([a-z_]+)\}", part)
    }
    unknown = fields - ALLOWED_FIELDS
    if unknown:
        raise argparse.ArgumentTypeError(
            "unknown command template fields: " + ", ".join(sorted(unknown)))
    if "prompt" not in fields:
        raise argparse.ArgumentTypeError(
            "command template must contain the {prompt} field")
    return command


def positive_timeout(value: str) -> float:
    parsed = float(value)
    if not 0 < parsed <= MAX_TIMEOUT_SECONDS:
        raise argparse.ArgumentTypeError(
            f"must be between 0 and {MAX_TIMEOUT_SECONDS:g}")
    return parsed


def output_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= MAX_OUTPUT_BYTES:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_OUTPUT_BYTES}")
    return parsed


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, AttributeError):
            process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def run_bounded(command: list[str], cwd: Path, timeout: float,
                maximum: int) -> dict:
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True)
    except OSError as exc:
        return {
            "returncode": None,
            "timed_out": False,
            "output_truncated": False,
            "duration_seconds": round(time.monotonic() - started, 6),
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = False
    truncated = False
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                stop_process(process)
                break
            events = selector.select(min(remaining, 0.25))
            if not events:
                if process.poll() is not None:
                    continue
                continue
            for key, _events in events:
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                used = sum(len(value) for value in buffers.values())
                available = maximum - used
                if len(chunk) > available:
                    buffers[key.data].extend(chunk[:max(available, 0)])
                    truncated = True
                    stop_process(process)
                    break
                buffers[key.data].extend(chunk)
            if truncated:
                break
        if process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                stop_process(process)
            else:
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    stop_process(process)
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    return {
        "returncode": process.returncode,
        "timed_out": timed_out,
        "output_truncated": truncated,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout": buffers["stdout"].decode("utf-8", errors="replace"),
        "stderr": buffers["stderr"].decode("utf-8", errors="replace"),
        "stdout_bytes": len(buffers["stdout"]),
        "stderr_bytes": len(buffers["stderr"]),
    }


def repository_revision() -> str | None:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
        text=True, timeout=10)
    revision = proc.stdout.strip()
    return revision if proc.returncode == 0 and re.fullmatch(
        r"[0-9a-f]{40}", revision) else None


def skill_record(skill_name: str, cases_path: Path) -> dict:
    skill_path = cases_path.parent.parent
    skill_file = skill_path / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")
    return {
        "name": skill_name,
        "path": str(skill_path.resolve()),
        "repository_revision": repository_revision(),
        "skill_sha256": sha256_text(content),
    }


def prepare_skill_snapshot(skill_path: Path, destination: Path) -> Path:
    if any(path.is_symlink() for path in skill_path.rglob("*")):
        raise EvalError("skill snapshots do not follow symbolic links")
    snapshot = destination / skill_path.name
    shutil.copytree(
        skill_path, snapshot,
        ignore=shutil.ignore_patterns("evals", "__pycache__", "*.pyc", "*.pyo"))
    if (snapshot / "evals").exists():
        raise EvalError("isolated skill snapshot unexpectedly contains eval cases")
    return snapshot


def reject_expectation_leak(template: list[str], cases: list[dict]) -> None:
    command_text = "\n".join(template)
    leaked = sorted({
        expectation
        for case in cases
        for expectation in case["expected"]
        if expectation in command_text
    })
    if leaked:
        raise EvalError(
            "command template contains eval expectation text for: "
            + ", ".join(case["id"] for case in cases
                        if any(item in command_text for item in case["expected"])))


def render_command(template: list[str], case: dict, skill: dict) -> list[str]:
    values = {
        "prompt": case["prompt"],
        "skill_path": skill["path"],
        "skill_name": skill["name"],
        "case_id": case["id"],
    }
    return [
        replace_template_fields(part, values)
        for part in template
    ]


def replace_template_fields(value: str, fields: dict[str, str]) -> str:
    for name, replacement in fields.items():
        value = value.replace("{" + name + "}", replacement)
    return value


def recorded_command(command: list[str], prompt: str, prompt_hash: str) -> list[str]:
    marker = f"<prompt-sha256:{prompt_hash}>"
    return [part.replace(prompt, marker) for part in command]


def parse_protocol(output: str) -> dict:
    try:
        data = json.loads(output)
    except json.JSONDecodeError as exc:
        raise EvalError(f"client stdout is not one JSON object: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("triggered"), bool):
        raise EvalError("client result must contain boolean triggered")
    selected = data.get("selected_skills", [])
    if (not isinstance(selected, list)
            or any(not isinstance(value, str) or not NAME_RE.fullmatch(value)
                   for value in selected)
            or len(selected) != len(set(selected))):
        raise EvalError("client result has invalid selected_skills")
    publication = data.get("publication_state", "not-requested")
    if publication not in PUBLICATION_STATES:
        raise EvalError("client result has invalid publication_state")
    return {
        "triggered": data["triggered"],
        "selected_skills": selected,
        "publication_state": publication,
    }


def evaluate_protocol(case: dict, protocol: dict) -> list[dict]:
    checks = [{
        "name": "activation",
        "passed": protocol["triggered"] == case["should_trigger"],
    }]
    expected_skills = case.get("expected_skills")
    if expected_skills is not None:
        checks.append({
            "name": "routing",
            "passed": sorted(protocol["selected_skills"]) == sorted(expected_skills),
        })
    boundary = case.get("publication_boundary")
    if boundary == "not-requested":
        passed = protocol["publication_state"] == "not-requested"
    elif boundary in {"stop-before-publish", "live-procedure-required"}:
        passed = protocol["publication_state"] == "stopped"
    else:
        passed = True
    if boundary is not None:
        checks.append({"name": "publication-boundary", "passed": passed})
    return checks


def select_cases(data: dict, identifiers: list[str]) -> list[dict]:
    if not identifiers:
        return data["cases"]
    selected = [case for case in data["cases"] if case["id"] in identifiers]
    missing = set(identifiers) - {case["id"] for case in selected}
    if missing:
        raise EvalError("unknown case ids: " + ", ".join(sorted(missing)))
    return selected


def run_case(template: list[str], case: dict, skill: dict, cwd: Path,
             timeout: float, maximum: int) -> dict:
    prompt_hash = sha256_text(case["prompt"])
    command = render_command(template, case, skill)
    execution = run_bounded(command, cwd, timeout, maximum)
    protocol = None
    error = None
    checks: list[dict] = []
    if (execution["returncode"] == 0 and not execution["timed_out"]
            and not execution["output_truncated"]):
        try:
            protocol = parse_protocol(execution["stdout"])
            checks = evaluate_protocol(case, protocol)
        except EvalError as exc:
            error = str(exc)
    else:
        error = "client execution did not complete successfully"
    return {
        "id": case["id"],
        "prompt_sha256": prompt_hash,
        "command": recorded_command(command, case["prompt"], prompt_hash),
        "execution": execution,
        "protocol": protocol,
        "checks": checks,
        "error": error,
        "ok": error is None and all(check["passed"] for check in checks),
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def emit(report: dict, output: Path | None) -> None:
    content = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
    if output is None:
        sys.stdout.write(content)
    else:
        atomic_write(output, content)
        print(output)


def static_report(paths: list[Path]) -> dict:
    records = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            errors = validate_case_data(data, str(path))
        except (OSError, json.JSONDecodeError) as exc:
            data = {}
            errors = [f"{path}: {exc}"]
        records.append({
            "path": str(path),
            "skill": data.get("skill"),
            "cases": len(data.get("cases", [])) if isinstance(
                data.get("cases"), list) else 0,
            "errors": errors,
            "ok": not errors,
        })
    return {
        "schema_version": 1,
        "mode": "static",
        "generated_at": utc_now(),
        "complete": True,
        "ok": all(record["ok"] for record in records),
        "case_files": records,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="mode", required=True)
    static = subparsers.add_parser("static", help="validate eval cases without a client")
    static.add_argument("cases", nargs="*", type=Path)
    static.add_argument("--output", type=Path)
    run = subparsers.add_parser("run", help="run an external client command per case")
    run.add_argument("--cases", type=Path, required=True)
    run.add_argument("--case", action="append", default=[])
    run.add_argument("--command-template", type=parse_command_template, required=True)
    run.add_argument("--client", required=True)
    run.add_argument("--client-version", required=True)
    run.add_argument("--cwd", type=Path)
    run.add_argument("--timeout", type=positive_timeout,
                     default=DEFAULT_TIMEOUT_SECONDS)
    run.add_argument("--max-output-bytes", type=output_limit,
                     default=DEFAULT_MAX_OUTPUT_BYTES)
    run.add_argument("--output", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.mode == "static":
        report = static_report(case_paths(args.cases))
    else:
        cases_path = args.cases.resolve()
        data = load_cases(cases_path)
        skill = skill_record(data["skill"], cases_path)
        cases = select_cases(data, args.case)
        reject_expectation_leak(args.command_template, cases)
        with tempfile.TemporaryDirectory(prefix="gentoo-skill-eval-") as temporary:
            snapshot = prepare_skill_snapshot(
                Path(skill["path"]), Path(temporary))
            execution_skill = {**skill, "path": str(snapshot)}
            execution_cwd = (args.cwd.resolve() if args.cwd is not None
                             else snapshot.parent)
            results = [
                run_case(args.command_template, case, execution_skill,
                         execution_cwd, args.timeout, args.max_output_bytes)
                for case in cases
            ]
        report = {
            "schema_version": 1,
            "mode": "external",
            "generated_at": utc_now(),
            "client": {"name": args.client, "version": args.client_version},
            "skill": skill,
            "command_template": args.command_template,
            "limits": {
                "timeout_seconds": args.timeout,
                "max_output_bytes": args.max_output_bytes,
            },
            "isolation": {
                "eval_files_excluded": True,
                "operating_system_sandbox": False,
            },
            "complete": all(
                item["execution"]["returncode"] is not None
                and not item["execution"]["timed_out"]
                and not item["execution"]["output_truncated"]
                for item in results),
            "ok": all(item["ok"] for item in results),
            "cases": results,
        }
    emit(report, args.output)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
