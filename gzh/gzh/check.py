from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

from gzh.qa_evidence import identify_input


READ_ONLY_GATES = frozenset({
    "artifacts", "binary", "deps", "doctor", "image", "lint", "qa",
})
MUTATING_GATES = frozenset({
    "bump", "commit", "drop-old", "manifest", "merge", "push", "recommit", "test",
    "urls",
})


@dataclass(frozen=True)
class Gate:
    name: str
    runner: Callable[[Path], Mapping] | None = None
    required: bool = True
    skip_reason: str | None = None


def _gate_record(gate: Gate) -> dict:
    return {
        "name": gate.name,
        "required": gate.required,
        "state": "pending",
        "ok": False,
        "complete": False,
        "truncated": False,
        "skipped": False,
        "skip_reason": None,
        "result": None,
    }


def _validate_gates(gates: Sequence[Gate]) -> list[str]:
    errors: list[str] = []
    if not gates:
        errors.append("at least one read-only gate is required")
    names: set[str] = set()
    for gate in gates:
        if gate.name in names:
            errors.append(f"duplicate gate: {gate.name}")
        names.add(gate.name)
        if gate.name in MUTATING_GATES:
            errors.append(f"mutating gate is forbidden: {gate.name}")
        elif gate.name not in READ_ONLY_GATES:
            errors.append(f"gate is not in the read-only allowlist: {gate.name}")
        if gate.runner is None and not gate.skip_reason:
            errors.append(f"gate has no runner or skip reason: {gate.name}")
        if gate.runner is not None and gate.skip_reason:
            errors.append(f"gate cannot have both a runner and skip reason: {gate.name}")
    return errors


def _input_changed(before: Mapping, after: Mapping) -> bool:
    return (after.get("kind") != before.get("kind")
            or after.get("git_revision") != before.get("git_revision")
            or after.get("git_status") != before.get("git_status")
            or after.get("sha256") != before.get("sha256"))


def run_read_only_checks(root: Path, gates: Sequence[Gate]) -> dict:
    """Run allowlisted gates and stop immediately when evidence is incomplete."""
    resolved = root.expanduser().resolve()
    records = [_gate_record(gate) for gate in gates]
    report = {
        "schema": 1,
        "ok": False,
        "complete": False,
        "truncated": False,
        "state": "incomplete",
        "input": identify_input(resolved),
        "gates": records,
        "errors": [],
    }
    validation_errors = _validate_gates(gates)
    if report["input"]["kind"] != "directory":
        validation_errors.append("check root must be an existing directory")
    if validation_errors:
        report["errors"] = [
            {"stage": "configuration", "message": message}
            for message in validation_errors
        ]
        for record in records:
            record.update({
                "state": "skipped",
                "skipped": True,
                "skip_reason": "invalid gate configuration",
            })
        return report

    all_ok = True
    for index, (gate, record) in enumerate(zip(gates, records)):
        if gate.runner is None:
            record.update({
                "state": "skipped",
                "ok": not gate.required,
                "complete": not gate.required,
                "skipped": True,
                "skip_reason": gate.skip_reason,
            })
            if gate.required:
                all_ok = False
                report["errors"].append({
                    "stage": gate.name,
                    "message": f"required gate was skipped: {gate.skip_reason}",
                })
                _block_remaining(records, index + 1, gate.name)
                return report
            continue
        try:
            result = gate.runner(resolved)
        except Exception as exc:
            after = identify_input(resolved)
            report["input_after"] = after
            record.update({
                "state": "incomplete",
                "result": None,
            })
            report["errors"].append({
                "stage": gate.name,
                "type": type(exc).__name__,
                "message": str(exc),
            })
            if _input_changed(report["input"], after):
                report["errors"].append({
                    "stage": "read-only-boundary",
                    "message": "gate execution changed the repository input",
                })
            _block_remaining(records, index + 1, gate.name)
            return report
        after = identify_input(resolved)
        report["input_after"] = after
        if _input_changed(report["input"], after):
            record.update({
                "state": "incomplete",
                "result": dict(result) if isinstance(result, Mapping) else None,
            })
            report["errors"].append({
                "stage": "read-only-boundary",
                "message": f"gate changed the repository input: {gate.name}",
            })
            _block_remaining(records, index + 1, gate.name)
            return report
        if not isinstance(result, Mapping):
            result = {
                "ok": False,
                "complete": False,
                "truncated": False,
                "error": "gate did not return a mapping",
            }
        gate_ok = result.get("ok") is True
        gate_truncated = result.get("truncated") is True
        gate_complete = result.get("complete") is True and not gate_truncated
        record.update({
            "state": ("passed" if gate_ok and gate_complete
                      else "failed" if gate_complete else "incomplete"),
            "ok": gate_ok,
            "complete": gate_complete,
            "truncated": gate_truncated,
            "result": dict(result),
        })
        all_ok = all_ok and gate_ok
        report["truncated"] = report["truncated"] or gate_truncated
        if not gate_complete or gate_truncated:
            report["errors"].append({
                "stage": gate.name,
                "message": "gate evidence is incomplete",
            })
            _block_remaining(records, index + 1, gate.name)
            return report

    after = identify_input(resolved)
    report["input_after"] = after
    if _input_changed(report["input"], after):
        report["errors"].append({
            "stage": "read-only-boundary",
            "message": "gate execution changed the repository input",
        })
        return report
    report["complete"] = True
    report["ok"] = all_ok
    report["state"] = "passed" if all_ok else "failed"
    return report


def _block_remaining(records: list[dict], start: int, gate_name: str) -> None:
    for record in records[start:]:
        record.update({
            "state": "skipped",
            "skipped": True,
            "skip_reason": f"blocked by incomplete gate: {gate_name}",
        })
