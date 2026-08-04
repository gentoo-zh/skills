from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest


ROOT = Path(__file__).resolve().parent.parent
PATH = (ROOT / ".agents" / "skills" / "gzh-maintain-skills"
        / "scripts" / "evidence_store.py")


def load_module():
    spec = importlib.util.spec_from_file_location("evidence_store_test", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


evidence = load_module()


def candidate(authority="candidate-history"):
    return {
        "candidate_key": "qa-candidate",
        "topic": "qa",
        "scope": "example-overlay",
        "authority": authority,
        "adapter_id": "example-adapter",
        "source_id": f"candidate-history:{'a' * 40}",
        "source_url": "https://example.invalid/overlay",
        "source_revision": "a" * 40,
        "policy_status": "not-established",
        "summary": "A change that still requires primary confirmation.",
    }


def checklist():
    return {
        "claim_type": "portable-gentoo",
        "old_behavior": "The old gate accepted the failing case.",
        "new_behavior": "The new gate rejects the failing case.",
        "scope": "example-overlay",
        "pass_condition": "The supported fixture returns zero.",
        "fail_condition": "The unsupported fixture returns nonzero.",
        "regression": "tests/test_gate.py::test_rejects_unsupported",
        "conflict_check": "No higher-authority source conflicts.",
        "rollback": "Remove the gate and its regression together.",
    }


def link_evidence(store, observation):
    return store.link_reviewed_evidence(
        "qa-candidate", observation["fingerprint"],
        "Reviewed this evidence for the candidate claim.")


def report():
    return {
        "schema": 1,
        "generated_at": "2026-08-04T00:00:00Z",
        "ok": True,
        "complete": True,
        "truncated": False,
        "source_records": [{
            "id": "gentoo-standard",
            "authority": "gentoo-standard",
            "url": "https://example.invalid/standard",
            "revision": "b" * 64,
            "state": "current",
            "validated": True,
            "topics": ["qa"],
        }, {
            "id": f"candidate-history:{'a' * 40}",
            "source_id": f"candidate-history:{'a' * 40}",
            "authority": "candidate-history",
            "url": "https://example.invalid/overlay",
            "revision": "a" * 40,
            "state": "observed",
            "validated": False,
            "role": "candidate",
            "adapter_id": "example-adapter",
            "canonical_repository": "example-overlay",
            "topics": ["qa"],
        }],
        "candidates": [candidate()],
    }


def test_ingest_is_idempotent_and_keeps_full_payload(tmp_path):
    original = report()
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        first = store.ingest(original, "qa-style")
        second = store.ingest(original, "qa-style")

        assert first["observations_ingested"] == 2
        assert first["candidates_ingested"] == 1
        assert second["observations_ingested"] == 0
        assert second["candidates_ingested"] == 0
        assert first["review"]["status"] == "review-required"
        assert first["review"] == second["review"]
        stored = store.connection.execute(
            "SELECT report_json, report_sha256 FROM runs").fetchone()
        assert json.loads(stored["report_json"]) == original
        assert stored["report_sha256"] == evidence.sha256_json(original)


def test_review_status_is_deterministic_and_clears_only_after_resolution(tmp_path):
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")

        first = store.review_status()
        second = store.review_status()

        assert first == second
        assert first["status"] == "review-required"
        assert first["review_required"] is True
        assert first["counts"] == {
            "candidate": 1,
            "reviewed": 0,
            "promoted": 0,
            "rejected": 0,
            "open": 1,
            "total": 1,
        }
        assert first["summary_complete"] is True
        assert first["candidates_emitted"] == 1
        assert first["candidates_omitted"] == 0
        assert first["candidates"] == [{
            "candidate_key": "qa-candidate",
            "state": "candidate",
            "topic": "qa",
            "scope": "example-overlay",
            "summary": "A change that still requires primary confirmation.",
            "source_id": f"candidate-history:{'a' * 40}",
            "source_url": "https://example.invalid/overlay",
            "source_revision": "a" * 40,
            "policy_status": "not-established",
        }]
        markdown = evidence.render_review_markdown(first)
        assert "- Status: `review-required`" in markdown
        assert "A change that still requires primary confirmation." in markdown

        store.transition(
            "qa-candidate", "candidate", "rejected",
            "Primary evidence does not support this candidate.")
        resolved = store.review_status()

        assert resolved["status"] == "current"
        assert resolved["review_required"] is False
        assert resolved["counts"]["rejected"] == 1
        assert resolved["candidates"] == []


def test_review_summary_is_bounded_and_escapes_external_markdown(tmp_path):
    data = report()
    data["candidates"][0]["summary"] = (
        "@maintainer | <script> [link](https://example.invalid) `code` "
        + "x" * 400)
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        review = store.review_status()

    assert len(review["candidates"][0]["summary"]) == 240
    markdown = evidence.render_review_markdown(review)
    assert "&#64;maintainer" in markdown
    assert "&#124;" in markdown
    assert "&lt;script&gt;" in markdown
    assert "&#91;link&#93;&#40;https://example.invalid&#41;" in markdown
    assert "&#96;code&#96;" in markdown
    assert "@maintainer" not in markdown


def test_review_markdown_strips_control_and_directional_characters(tmp_path):
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")
        review = store.review_status()

    controls = "".join(chr(value) for value in (
        *range(0x20), *range(0x7f, 0xa0)))
    directional = "".join(sorted(evidence.BIDI_CONTROL_CHARACTERS))
    for field in (
            "candidate_key", "state", "topic", "scope", "source_revision",
            "summary"):
        review["candidates"][0][field] += controls + directional + "visible"

    markdown = evidence.render_review_markdown(review)
    candidate_line = next(
        line for line in markdown.splitlines() if "visible" in line)

    assert all(character not in candidate_line for character in controls)
    assert all(character not in candidate_line for character in directional)
    assert candidate_line.count("visible") == 6


def test_review_summary_reports_deterministic_omissions(tmp_path, monkeypatch):
    first = report()
    second = report()
    second["generated_at"] = "2026-08-05T00:00:00Z"
    second["candidates"][0]["candidate_key"] = "zz-candidate"
    monkeypatch.setattr(evidence, "MAX_REVIEW_SUMMARY_CANDIDATES", 1)
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(first, "qa-style")
        store.ingest(second, "qa-style")
        review = store.review_status()

    assert review["summary_complete"] is False
    assert review["candidates_emitted"] == 1
    assert review["candidates_omitted"] == 1
    assert review["candidates"][0]["candidate_key"] == "qa-candidate"


def test_candidate_cannot_skip_review_or_use_secondary_observation(tmp_path):
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")
        with pytest.raises(ValueError, match="invalid transition"):
            store.transition(
                "qa-candidate", "candidate", "promoted", "unsupported shortcut")

        store.transition(
            "qa-candidate", "candidate", "reviewed", "Reproduced locally.")
        secondary = next(
            item for item in store.list_observations()
            if item["authority"] == "candidate-history")
        store.link_reviewed_evidence(
            "qa-candidate", secondary["fingerprint"],
            "Reviewed the candidate history record.")
        with pytest.raises(ValueError, match="primary"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "No primary evidence was attached.",
                evidence_fingerprint=secondary["fingerprint"],
                checklist=checklist())


def two_candidate_store(store):
    first = report()
    second = report()
    second["generated_at"] = "2026-08-05T00:00:00Z"
    second["candidates"][0]["candidate_key"] = "second-candidate"
    store.ingest(first, "qa-style")
    store.ingest(second, "qa-style")


def test_batch_transition_applies_explicit_decisions_atomically(tmp_path):
    manifest = {
        "schema": 1,
        "decisions": [{
            "candidate_key": "qa-candidate",
            "from_state": "candidate",
            "to_state": "reviewed",
            "reason": "Primary confirmation is still required.",
        }, {
            "candidate_key": "second-candidate",
            "from_state": "candidate",
            "to_state": "rejected",
            "reason": "The change is package-specific precedent.",
        }],
    }
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        two_candidate_store(store)
        result = store.batch_transition(manifest)
        states = {
            row["candidate_key"]: row["state"]
            for row in store.list_candidates()
        }
        transitions = [dict(row) for row in store.connection.execute(
            "SELECT * FROM candidate_transitions ORDER BY transition_id")]

    assert result == {
        "schema": 1,
        "applied": 2,
        "decisions": manifest["decisions"],
    }
    assert states == {
        "qa-candidate": "reviewed",
        "second-candidate": "rejected",
    }
    assert [item["candidate_key"] for item in transitions] == [
        "qa-candidate", "second-candidate"]
    assert [item["reason"] for item in transitions] == [
        decision["reason"] for decision in manifest["decisions"]]
    assert {item["changed_at"] for item in transitions} == {
        transitions[0]["changed_at"]}


def test_batch_transition_validates_every_candidate_before_mutation(tmp_path):
    manifest = {
        "schema": 1,
        "decisions": [{
            "candidate_key": "qa-candidate",
            "from_state": "candidate",
            "to_state": "rejected",
            "reason": "This decision is valid by itself.",
        }, {
            "candidate_key": "missing-candidate",
            "from_state": "candidate",
            "to_state": "rejected",
            "reason": "This exact key does not exist.",
        }],
    }
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        two_candidate_store(store)
        with pytest.raises(ValueError, match="unknown candidate"):
            store.batch_transition(manifest)
        states = {row["state"] for row in store.list_candidates()}
        transitions = store.connection.execute(
            "SELECT COUNT(*) AS count FROM candidate_transitions").fetchone()[
                "count"]

    assert states == {"candidate"}
    assert transitions == 0


def test_batch_transition_rolls_back_an_apply_failure(tmp_path):
    manifest = {
        "schema": 1,
        "decisions": [{
            "candidate_key": key,
            "from_state": "candidate",
            "to_state": "rejected",
            "reason": "Package history does not establish policy.",
        } for key in ("qa-candidate", "second-candidate")],
    }
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        two_candidate_store(store)
        store.connection.execute("""
            CREATE TRIGGER reject_second_batch_update
            BEFORE UPDATE ON candidates
            WHEN OLD.candidate_key = 'second-candidate'
            BEGIN
                SELECT RAISE(ABORT, 'forced batch failure');
            END
        """)
        store.connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="forced batch failure"):
            store.batch_transition(manifest)
        states = {row["state"] for row in store.list_candidates()}
        transitions = store.connection.execute(
            "SELECT COUNT(*) AS count FROM candidate_transitions").fetchone()[
                "count"]

    assert states == {"candidate"}
    assert transitions == 0


@pytest.mark.parametrize(("mutate", "message"), [
    (lambda value: value.update({"schema": 2}), "schema"),
    (lambda value: value.update({"schema": 1.0}), "schema"),
    (lambda value: value.update({"extra": True}), "fields"),
    (lambda value: value.update({"decisions": []}), "between 1 and"),
    (lambda value: value["decisions"].append(
        dict(value["decisions"][0])), "duplicate"),
    (lambda value: value["decisions"][0].update(
        {"to_state": "promoted", "from_state": "reviewed"}),
     "cannot promote"),
    (lambda value: value["decisions"][0].update(
        {"reason": "x" * (evidence.MAX_BATCH_REASON_CHARACTERS + 1)}),
     "reason exceeds"),
])
def test_batch_transition_manifest_rejects_invalid_contract(mutate, message):
    manifest = {
        "schema": 1,
        "decisions": [{
            "candidate_key": "qa-candidate",
            "from_state": "candidate",
            "to_state": "rejected",
            "reason": "Package history does not establish policy.",
        }],
    }
    mutate(manifest)

    with pytest.raises(ValueError, match=message):
        evidence.validate_batch_transition_manifest(manifest)


def test_batch_transition_manifest_bounds_decision_count():
    decision = {
        "candidate_key": "candidate-0",
        "from_state": "candidate",
        "to_state": "rejected",
        "reason": "Package history does not establish policy.",
    }
    manifest = {
        "schema": 1,
        "decisions": [
            {**decision, "candidate_key": f"candidate-{number}"}
            for number in range(evidence.MAX_BATCH_TRANSITIONS + 1)
        ],
    }

    with pytest.raises(ValueError, match="between 1 and"):
        evidence.validate_batch_transition_manifest(manifest)


def test_batch_transition_cli_reads_bounded_manifest(tmp_path, monkeypatch, capsys):
    database = tmp_path / "evidence.db"
    manifest = tmp_path / "batch.json"
    manifest.write_text(json.dumps({
        "schema": 1,
        "decisions": [{
            "candidate_key": "qa-candidate",
            "from_state": "candidate",
            "to_state": "rejected",
            "reason": "Package history does not establish policy.",
        }],
    }), encoding="utf-8")
    with evidence.EvidenceStore(database) as store:
        store.ingest(report(), "qa-style")
    monkeypatch.setattr("sys.argv", [
        str(PATH), "--db", str(database), "batch-transition", str(manifest),
    ])

    assert evidence.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["applied"] == 1
    with evidence.EvidenceStore(database) as store:
        assert store.list_candidates()[0]["state"] == "rejected"


def test_batch_transition_cli_rejects_oversized_manifest(tmp_path):
    manifest = tmp_path / "batch.json"
    manifest.write_bytes(b" " * (evidence.MAX_BATCH_MANIFEST_BYTES + 1))

    with pytest.raises(ValueError, match="exceeds"):
        evidence.load_batch_transition_manifest(manifest)


@pytest.mark.parametrize(("raw_manifest", "duplicate_key"), [
    (
        '{"schema":1,"schema":1,"decisions":['
        '{"candidate_key":"qa-candidate","from_state":"candidate",'
        '"to_state":"rejected","reason":"Not portable."}]}',
        "schema",
    ),
    (
        '{"schema":1,"decisions":['
        '{"candidate_key":"qa-candidate",'
        '"candidate_key":"second-candidate","from_state":"candidate",'
        '"to_state":"rejected","reason":"Not portable."}]}',
        "candidate_key",
    ),
])
def test_batch_transition_cli_rejects_duplicate_json_keys_cleanly(
        tmp_path, raw_manifest, duplicate_key):
    database = tmp_path / "evidence.db"
    manifest = tmp_path / "batch.json"
    manifest.write_text(raw_manifest, encoding="utf-8")
    with evidence.EvidenceStore(database) as store:
        store.ingest(report(), "qa-style")

    result = subprocess.run([
        sys.executable, str(PATH), "--db", str(database),
        "batch-transition", str(manifest),
    ], check=False, capture_output=True, text=True)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        f"error: duplicate JSON object key: {duplicate_key!r}\n")
    with evidence.EvidenceStore(database) as store:
        assert store.list_candidates()[0]["state"] == "candidate"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM candidate_transitions").fetchone()[0] == 0


def test_primary_evidence_must_include_revision(tmp_path):
    data = report()
    data["source_records"][0]["revision"] = None
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="immutable revision"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Normative behavior confirmed.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=checklist())


def test_promotion_requires_complete_checklist_and_stored_primary_evidence(tmp_path):
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        with pytest.raises(ValueError, match="checklist is incomplete"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Normative behavior confirmed.",
                evidence_fingerprint=primary["fingerprint"],
                checklist={"scope": "overlay"})
        with pytest.raises(ValueError, match="not linked by explicit review"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Discovery alone cannot establish reviewed evidence.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=checklist())
        link_evidence(store, primary)
        result = store.transition(
            "qa-candidate", "reviewed", "promoted",
            "Normative behavior confirmed.",
            evidence_fingerprint=primary["fingerprint"],
            checklist=checklist())
        assert result["state"] == "promoted"
        assert result["promotion_fingerprint"] == primary["fingerprint"]


def test_incomplete_report_is_recorded_as_incomplete(tmp_path):
    data = report()
    data["complete"] = False
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        result = store.ingest(data, "qa-style")
    assert result["status"] == "incomplete"
    assert result["candidates_ingested"] == 0
    assert result["candidates_skipped"] == 1


def test_missing_candidate_provenance_is_rejected(tmp_path):
    data = report()
    del data["candidates"][0]["source_url"]
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        with pytest.raises(ValueError, match="lacks topic, scope, or provenance"):
            store.ingest(data, "qa-style")


def test_same_observation_is_linked_to_every_run(tmp_path):
    first = report()
    second = report()
    second["generated_at"] = "2026-08-05T00:00:00Z"
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(first, "qa-style")
        store.ingest(second, "qa-style")
        standard = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        links = store.connection.execute(
            "SELECT COUNT(*) AS count FROM run_observations WHERE fingerprint = ?",
            (standard["fingerprint"],)).fetchone()["count"]
    assert links == 2


def test_candidate_key_collision_is_rejected(tmp_path):
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")
        changed = report()
        changed["generated_at"] = "2026-08-05T00:00:00Z"
        changed["candidates"][0]["summary"] = "Different content."
        with pytest.raises(ValueError, match="collision"):
            store.ingest(changed, "qa-style")


def test_candidate_cannot_create_its_own_primary_observation(tmp_path):
    data = report()
    data["source_records"] = data["source_records"][:1]
    data["candidates"][0]["authority"] = "gentoo-standard"
    data["candidates"][0]["source_revision"] = "c" * 40
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        with pytest.raises(ValueError, match="independently collected"):
            store.ingest(data, "qa-style")


def test_candidate_cannot_relabel_primary_discovery_as_history(tmp_path):
    data = report()
    primary = data["source_records"][0]
    data["source_records"] = [primary]
    data["candidates"][0].update({
        "authority": primary["authority"],
        "source_id": primary["id"],
        "source_url": primary["url"],
        "source_revision": primary["revision"],
    })
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        with pytest.raises(ValueError, match="candidate-history"):
            store.ingest(data, "qa-style")


def test_missing_explicit_completeness_skips_candidates(tmp_path):
    data = report()
    del data["complete"]
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        result = store.ingest(data, "qa-style")
    assert result["status"] == "incomplete"
    assert result["candidates_skipped"] == 1


def test_latest_cursor_requires_matching_complete_passed_scope(tmp_path):
    data = report()
    data["history"] = {"after_revision": "a" * 40}
    data["scope"] = {
        "adapter_id": "example-adapter",
        "canonical_repository": "example/overlay",
        "configured_origin": "https://example.invalid/overlay",
        "location": "https://example.invalid/overlay",
        "repo_name": "example-overlay",
    }
    data["source_records"].append({
        "id": "scope-cursor",
        "source_id": "scope-cursor",
        "authority": "repository-cursor",
        "url": "https://example.invalid/overlay",
        "revision": "c" * 40,
        "state": "observed",
        "validated": False,
        "role": "cursor",
        "topics": [],
        "adapter_id": "example-adapter",
        "canonical_repository": "example/overlay",
        "repo_name": "example-overlay",
        "complete": True,
        "truncated": False,
    })
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        cursor = store.latest_cursor("example-adapter", "example/overlay")
        assert cursor["revision"] == "c" * 40
        assert store.latest_cursor("other-adapter", "example/overlay") is None
        assert store.latest_cursor("example-adapter", "other/overlay") is None


def test_latest_cursor_ignores_incomplete_run(tmp_path):
    data = report()
    data["complete"] = False
    data["history"] = {"after_revision": "a" * 40}
    data["source_records"].append({
        "id": "scope-cursor",
        "authority": "repository-cursor",
        "url": "https://example.invalid/overlay",
        "revision": "c" * 40,
        "state": "observed",
        "validated": False,
        "topics": [],
        "adapter_id": "example-adapter",
        "canonical_repository": "example/overlay",
        "complete": False,
        "truncated": False,
    })
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        assert store.latest_cursor(
            "example-adapter", "example/overlay") is None


def cursor_report(adapter_id, repository, after_revision, revision, generated_at):
    url = f"https://example.invalid/{repository}"
    return {
        "schema": 1,
        "generated_at": generated_at,
        "ok": True,
        "complete": True,
        "truncated": False,
        "scope": {
            "adapter_id": adapter_id,
            "canonical_repository": repository,
            "configured_origin": url,
            "location": url,
            "repo_name": repository.rsplit("/", 1)[-1],
        },
        "history": {
            "after_revision": after_revision,
            "complete": True,
            "truncated": False,
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
            "adapter_id": adapter_id,
            "canonical_repository": repository,
            "repo_name": repository.rsplit("/", 1)[-1],
            "complete": True,
            "truncated": False,
        }],
        "candidates": [],
    }


def test_cursor_chain_rejects_stale_same_repository_replay(tmp_path):
    first = cursor_report(
        "adapter-a", "org/a", "1" * 40, "2" * 40,
        "2026-08-04T00:01:00Z")
    advance = cursor_report(
        "adapter-a", "org/a", "2" * 40, "3" * 40,
        "2026-08-04T00:03:00Z")
    stale = cursor_report(
        "adapter-a", "org/a", "1" * 40, "4" * 40,
        "2026-08-04T00:02:00Z")
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(first, "qa-style")
        store.ingest(advance, "qa-style")
        with pytest.raises(ValueError, match="current repository cursor"):
            store.ingest(stale, "qa-style")
        assert store.latest_cursor("adapter-a", "org/a")["revision"] == "3" * 40


def test_cursor_chains_are_isolated_between_repositories(tmp_path):
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(cursor_report(
            "adapter-a", "org/a", "1" * 40, "2" * 40,
            "2026-08-04T00:01:00Z"), "qa-style")
        store.ingest(cursor_report(
            "adapter-b", "org/b", "8" * 40, "9" * 40,
            "2026-08-04T00:02:00Z"), "qa-style")
        assert store.latest_cursor("adapter-a", "org/a")["revision"] == "2" * 40
        assert store.latest_cursor("adapter-b", "org/b")["revision"] == "9" * 40


def test_cursor_chain_serializes_two_connection_race(tmp_path, monkeypatch):
    database = tmp_path / "evidence.db"
    with evidence.EvidenceStore(database) as store:
        store.ingest(cursor_report(
            "adapter-a", "org/a", "1" * 40, "2" * 40,
            "2026-08-04T00:01:00Z"), "qa-style")

    first_report = cursor_report(
        "adapter-a", "org/a", "2" * 40, "3" * 40,
        "2026-08-04T00:02:00Z")
    second_report = cursor_report(
        "adapter-a", "org/a", "2" * 40, "4" * 40,
        "2026-08-04T00:03:00Z")
    first_validated = threading.Event()
    release_first = threading.Event()
    second_ready = threading.Event()
    release_second = threading.Event()
    original_validate = evidence.EvidenceStore._validate_cursor_advance
    results = []
    errors = []

    def pause_after_validation(store, data, sources):
        original_validate(store, data, sources)
        revision = sources[0]["revision"]
        if revision == "3" * 40:
            first_validated.set()
            assert release_first.wait(5)
        elif revision == "4" * 40:
            second_ready.set()
            assert release_second.wait(5)

    def ingest(data, trace_begin=False):
        try:
            with evidence.EvidenceStore(database) as store:
                if trace_begin:
                    store.connection.set_trace_callback(
                        lambda statement: second_ready.set()
                        if statement.strip().upper() == "BEGIN IMMEDIATE"
                        else None)
                results.append(store.ingest(data, "qa-style"))
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(
        evidence.EvidenceStore, "_validate_cursor_advance",
        pause_after_validation)
    first = threading.Thread(target=ingest, args=(first_report,))
    second = threading.Thread(target=ingest, args=(second_report, True))
    first.start()
    assert first_validated.wait(5)
    second.start()
    assert second_ready.wait(5)
    release_first.set()
    first.join(5)
    release_second.set()
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(results) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], ValueError)
    assert "current repository cursor" in str(errors[0])
    with evidence.EvidenceStore(database) as store:
        assert store.latest_cursor("adapter-a", "org/a")["revision"] == "3" * 40


def test_from_connection_bootstraps_schema_in_shared_transaction():
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("BEGIN IMMEDIATE")
        store = evidence.EvidenceStore.from_connection(connection)
        result = store.ingest(report(), "qa-style", manage_transaction=False)

        assert connection.in_transaction
        connection.commit()
        assert result["status"] == "passed"
        assert connection.execute(
            "SELECT COUNT(*) FROM runs").fetchone()[0] == 1
        store.close()
        connection.execute("SELECT 1")
    finally:
        connection.close()


def test_registered_but_unvalidated_source_cannot_support_promotion(tmp_path):
    data = report()
    data["source_records"][0]["state"] = "registered"
    data["source_records"][0]["validated"] = False
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="usable immutable revision"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Normative behavior confirmed.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=checklist())


def test_promotion_evidence_topic_must_match_candidate(tmp_path):
    data = report()
    data["source_records"][0]["topics"] = ["dependency"]
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="candidate topic"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Unrelated normative evidence must not promote the finding.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=checklist())


@pytest.mark.parametrize(("adapter_id", "canonical_repository"), [
    ("other-adapter", "example-overlay"),
    ("fixture", "other/overlay"),
])
def test_overlay_policy_must_match_adapter_and_repository(
        tmp_path, adapter_id, canonical_repository):
    data = report()
    data["source_records"][0].update({
        "authority": "overlay-policy",
        "adapter_id": adapter_id,
        "canonical_repository": canonical_repository,
    })
    policy_checklist = checklist()
    policy_checklist["claim_type"] = "repository-policy"
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "overlay-policy")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="adapter and canonical repository"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Policy from another overlay must not promote the finding.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=policy_checklist)


def test_authority_must_be_eligible_for_claim_type(tmp_path):
    policy_checklist = checklist()
    policy_checklist["claim_type"] = "repository-policy"
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="not eligible"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Portable evidence cannot establish repository policy.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=policy_checklist)


def test_later_evidence_requires_explicit_reviewed_link(tmp_path):
    later = report()
    later["generated_at"] = "2026-08-05T00:00:00Z"
    later["source_records"] = [later["source_records"][0]]
    later["source_records"][0]["revision"] = "c" * 64
    later["candidates"] = []
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(report(), "qa-style")
        store.ingest(later, "source-audit")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["revision"] == "c" * 64)
        with pytest.raises(ValueError, match="not linked"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Later evidence has not been reviewed for this candidate.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=checklist())
        link = store.link_reviewed_evidence(
            "qa-candidate", primary["fingerprint"],
            "Reviewed the current normative section for this candidate.")
        assert link["fingerprint"] == primary["fingerprint"]
        result = store.transition(
            "qa-candidate", "reviewed", "promoted",
            "Current normative behavior confirmed.",
            evidence_fingerprint=primary["fingerprint"],
            checklist=checklist())
        assert result["state"] == "promoted"


def test_upstream_primary_is_limited_to_matching_package_claim(tmp_path):
    data = report()
    data["candidates"][0]["package_atom"] = "cat/pkg"
    data["source_records"][0].update({
        "authority": "upstream-primary",
        "package_atom": "cat/other",
    })
    package_checklist = checklist()
    package_checklist["claim_type"] = "package-upstream-fact"
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "upstream-primary")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="candidate package"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Unrelated upstream evidence must not promote the finding.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=package_checklist)


def test_generic_gentoo_document_cannot_promote_package_upstream_fact(tmp_path):
    data = report()
    data["candidates"][0]["package_atom"] = "cat/pkg"
    package_checklist = checklist()
    package_checklist["claim_type"] = "package-upstream-fact"
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "gentoo-standard")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="not eligible"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Generic documentation cannot establish an upstream package fact.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=package_checklist)


def test_skill_architecture_is_limited_to_this_repository(tmp_path):
    data = report()
    data["source_records"][0]["authority"] = "skill-architecture"
    architecture_checklist = checklist()
    architecture_checklist["claim_type"] = "skill-architecture"
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        store.ingest(data, "qa-style")
        store.transition(
            "qa-candidate", "candidate", "reviewed", "Scope confirmed.")
        primary = next(
            item for item in store.list_observations()
            if item["authority"] == "skill-architecture")
        link_evidence(store, primary)
        with pytest.raises(ValueError, match="limited to this repository"):
            store.transition(
                "qa-candidate", "reviewed", "promoted",
                "Architecture evidence must not govern another repository.",
                evidence_fingerprint=primary["fingerprint"],
                checklist=architecture_checklist)


def test_compaction_bounds_routine_runs_and_preserves_candidate_provenance(
        tmp_path):
    database = tmp_path / "evidence.db"
    with evidence.EvidenceStore(database) as store:
        candidate_report = report()
        candidate_result = store.ingest(candidate_report, "qa-style")
        candidate_run = candidate_result["run_id"]
        source_fingerprint = store.list_candidates()[0]["source_fingerprint"]
        for number in range(6):
            routine = {
                "schema": 1,
                "generated_at": f"2026-08-{10 + number:02d}T00:00:00Z",
                "ok": True,
                "complete": True,
                "truncated": False,
                "source_records": [],
                "candidates": [],
            }
            store.ingest(routine, "maintenance-cycle")

        result = store.compact(keep_runs=2)
        run_ids = {row["run_id"] for row in store.connection.execute(
            "SELECT run_id FROM runs")}
        compacted_report = json.loads(store.connection.execute(
            "SELECT report_json FROM runs WHERE run_id = ?",
            (candidate_run,)).fetchone()["report_json"])
        source = store.connection.execute(
            "SELECT 1 FROM observations WHERE fingerprint = ?",
            (source_fingerprint,)).fetchone()

    assert result["runs_before"] == 7
    assert result["runs_kept"] == 3
    assert result["runs_removed"] == 4
    assert candidate_run in run_ids
    assert compacted_report["compacted"] is True
    assert source is not None

    with evidence.EvidenceStore(database) as store:
        repeated = store.ingest(candidate_report, "qa-style")
    assert repeated["candidates_ingested"] == 0


def test_candidate_backlog_ceiling_rolls_back_complete_report(
        tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "MAX_OPEN_CANDIDATES", 0)
    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        with pytest.raises(ValueError, match="backlog"):
            store.ingest(report(), "qa-style")
        runs = store.connection.execute(
            "SELECT COUNT(*) AS count FROM runs").fetchone()["count"]
        candidates = store.connection.execute(
            "SELECT COUNT(*) AS count FROM candidates").fetchone()["count"]
    assert runs == 0
    assert candidates == 0
