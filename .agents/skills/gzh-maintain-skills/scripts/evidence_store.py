#!/usr/bin/env python3
"""Store versioned maintenance evidence without promoting policy automatically."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
SKILLS_REPOSITORY = "gentoo-zh/skills"
MAX_OPEN_CANDIDATES = 512
MAX_TOTAL_CANDIDATES = 4096
MAX_STATE_BYTES = 96 * 1024 * 1024
PRIMARY_AUTHORITIES = {
    "overlay-policy",
    "gentoo-standard",
    "gentoo-tool",
    "upstream-primary",
    "skill-architecture",
}
AUTHORITY_CLAIM_TYPES = {
    "gentoo-standard": {"portable-gentoo", "package-gentoo-semantics"},
    "gentoo-tool": {"portable-gentoo", "package-gentoo-semantics"},
    "overlay-policy": {"repository-policy"},
    "upstream-primary": {"package-upstream-fact"},
    "skill-architecture": {"skill-architecture"},
}
PROMOTION_FIELDS = {
    "claim_type",
    "old_behavior",
    "new_behavior",
    "scope",
    "pass_condition",
    "fail_condition",
    "regression",
    "conflict_check",
    "rollback",
}
IMMUTABLE_REVISION_RE = re.compile(
    r"(?:[0-9a-f]{40}|[0-9a-f]{64}|[1-9][0-9]*)")
TRANSITIONS = {
    "candidate": {"reviewed", "rejected"},
    "reviewed": {"promoted", "rejected"},
    "promoted": set(),
    "rejected": set(),
}
SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS evidence_schema (
        version INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK (
            status IN ('passed', 'failed', 'incomplete')),
        report_sha256 TEXT NOT NULL,
        report_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS observations (
        fingerprint TEXT PRIMARY KEY,
        source_id TEXT NOT NULL,
        authority TEXT NOT NULL,
        url TEXT NOT NULL,
        revision TEXT,
        state TEXT NOT NULL,
        validated INTEGER NOT NULL CHECK (validated IN (0, 1)),
        topics_json TEXT NOT NULL,
        adapter_id TEXT,
        canonical_repository TEXT,
        package_atom TEXT,
        observed_at TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_observations (
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        fingerprint TEXT NOT NULL REFERENCES observations(fingerprint),
        PRIMARY KEY(run_id, fingerprint)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidates (
        candidate_key TEXT PRIMARY KEY,
        topic TEXT NOT NULL,
        scope TEXT NOT NULL,
        payload_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (
            state IN ('candidate', 'reviewed', 'promoted', 'rejected')),
        reason TEXT,
        discovered_run_id TEXT NOT NULL REFERENCES runs(run_id),
        source_fingerprint TEXT NOT NULL REFERENCES observations(fingerprint),
        promotion_fingerprint TEXT REFERENCES observations(fingerprint),
        checklist_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_transitions (
        transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
        old_state TEXT NOT NULL,
        new_state TEXT NOT NULL,
        reason TEXT NOT NULL,
        evidence_fingerprint TEXT REFERENCES observations(fingerprint),
        checklist_json TEXT,
        changed_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_evidence_links (
        candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
        fingerprint TEXT NOT NULL REFERENCES observations(fingerprint),
        review_reason TEXT NOT NULL,
        linked_at TEXT NOT NULL,
        PRIMARY KEY(candidate_key, fingerprint)
    )
    """,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalized_topics(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("source observation topics must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("source observation topics must contain nonempty strings")
    topics = [nonempty_text(item) for item in value]
    if any(topic is None for topic in topics):
        raise ValueError("source observation topics must contain nonempty strings")
    return sorted(set(topics))


def validate_checklist(checklist: dict | None) -> str:
    if not isinstance(checklist, dict):
        raise ValueError("promotion requires a structured checklist")
    missing = sorted(
        field for field in PROMOTION_FIELDS
        if field not in checklist or checklist[field] in (None, "", [], {}))
    if missing:
        raise ValueError(
            "promotion checklist is incomplete: " + ", ".join(missing))
    return canonical_json(checklist)


def stored_report_matches(row: sqlite3.Row, report_hash: str,
                          report_json: str) -> bool:
    if row["report_sha256"] != report_hash:
        return False
    if row["report_json"] == report_json:
        return True
    try:
        compacted = json.loads(row["report_json"])
    except json.JSONDecodeError:
        return False
    return (compacted.get("compacted") is True
            and compacted.get("original_report_sha256") == report_hash)


class EvidenceStore:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._owns_connection = True
        self.initialize()

    @classmethod
    def from_connection(cls, connection: sqlite3.Connection) -> "EvidenceStore":
        instance = cls.__new__(cls)
        instance.path = None
        instance.connection = connection
        instance.connection.row_factory = sqlite3.Row
        instance.connection.execute("PRAGMA foreign_keys = ON")
        instance._owns_connection = False
        instance.initialize(commit=not instance.connection.in_transaction)
        return instance

    def close(self) -> None:
        if self._owns_connection:
            self.connection.close()

    def __enter__(self) -> "EvidenceStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def initialize(self, *, commit: bool = True) -> None:
        for statement in SCHEMA_STATEMENTS:
            self.connection.execute(statement)
        rows = self.connection.execute(
            "SELECT version FROM evidence_schema").fetchall()
        if not rows:
            self.connection.execute(
                "INSERT INTO evidence_schema(version) VALUES (?)",
                (SCHEMA_VERSION,))
        elif len(rows) != 1 or rows[0]["version"] != SCHEMA_VERSION:
            raise ValueError("unsupported evidence database schema")
        if commit:
            self.connection.commit()

    def ingest(self, report: dict, kind: str, *,
               manage_transaction: bool = True) -> dict:
        if not isinstance(report, dict):
            raise ValueError("report must be a JSON object")
        report_hash = sha256_json(report)
        run_id = f"{kind}:{report_hash}"
        started_at = nonempty_text(report.get("generated_at")) or utc_now()
        status = "passed" if (
            report.get("ok") is True
            and report.get("complete") is True
            and report.get("truncated") is False
        ) else "failed"
        if report.get("complete") is not True or report.get("truncated") is True:
            status = "incomplete"
        sources = self._source_records(report)
        candidates = report.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("report candidates must be a list")

        inserted_observations = 0
        linked_observations = 0
        inserted_candidates = 0
        observation_index: dict[tuple[str, str, str | None], str] = {}
        cursor_report = status == "passed" and any(
            (item.get("id") or item.get("source_id")) == "scope-cursor"
            for item in sources)
        if manage_transaction:
            self.connection.execute("BEGIN IMMEDIATE")
        transaction = self.connection if manage_transaction else nullcontext()
        with transaction:
            existing = self.connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
            if existing and not stored_report_matches(
                    existing, report_hash, canonical_json(report)):
                raise ValueError("run id collision with different report content")
            if cursor_report and not existing:
                self._validate_cursor_advance(report, sources)
            self.connection.execute("""
                INSERT OR IGNORE INTO runs(
                    run_id, kind, started_at, completed_at, status,
                    report_sha256, report_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (run_id, kind, started_at, utc_now(), status,
                  report_hash, canonical_json(report)))
            for item in sources:
                inserted, linked, fingerprint, key = self._ingest_observation(
                    run_id, item, started_at)
                inserted_observations += inserted
                linked_observations += linked
                observation_index[key] = fingerprint
            if status == "passed":
                for item in candidates:
                    inserted, observation_inserted, linked = self._ingest_candidate(
                        run_id, item, started_at, observation_index)
                    inserted_candidates += inserted
                    inserted_observations += observation_inserted
                    linked_observations += linked
                counts = self.connection.execute(
                    """
                    SELECT COUNT(*) AS total,
                        SUM(CASE WHEN state IN ('candidate', 'reviewed')
                            THEN 1 ELSE 0 END) AS open
                    FROM candidates
                    """).fetchone()
                if (counts["total"] > MAX_TOTAL_CANDIDATES
                        or (counts["open"] or 0) > MAX_OPEN_CANDIDATES):
                    raise ValueError(
                        "candidate backlog exceeds the reviewed retention ceiling")
            if cursor_report:
                cursor = self.latest_cursor(
                    report["scope"]["adapter_id"],
                    report["scope"]["canonical_repository"])
                expected_revision = next(
                    item["revision"] for item in sources
                    if (item.get("id") or item.get("source_id"))
                    == "scope-cursor")
                if cursor is None or cursor["revision"] != expected_revision:
                    raise ValueError(
                        "passed cursor report did not persist its repository cursor")
        return {
            "run_id": run_id,
            "status": status,
            "observations_ingested": inserted_observations,
            "observation_links_ingested": linked_observations,
            "candidates_ingested": inserted_candidates,
            "candidates_skipped": len(candidates) if status != "passed" else 0,
        }

    def _validate_cursor_advance(self, report: dict,
                                 sources: list[dict]) -> None:
        cursors = [item for item in sources
                   if (item.get("id") or item.get("source_id"))
                   == "scope-cursor"]
        if len(cursors) != 1:
            raise ValueError("a passed cursor report must contain one scope cursor")
        cursor = cursors[0]
        adapter_id = nonempty_text(cursor.get("adapter_id"))
        repository = nonempty_text(cursor.get("canonical_repository"))
        revision = nonempty_text(cursor.get("revision"))
        history = report.get("history")
        scope = report.get("scope")
        after_revision = nonempty_text(
            history.get("after_revision") if isinstance(history, dict) else None)
        expected_url = (
            scope.get("canonical_origin") or scope.get("configured_origin")
            or scope.get("location")
            if isinstance(scope, dict) else None)
        if (not adapter_id or not repository or not expected_url
                or not IMMUTABLE_REVISION_RE.fullmatch(revision or "")
                or not IMMUTABLE_REVISION_RE.fullmatch(after_revision or "")):
            raise ValueError("cursor report lacks an immutable advance boundary")
        if (cursor.get("source_id") != "scope-cursor"
                or cursor.get("authority") != "repository-cursor"
                or cursor.get("url") != expected_url
                or cursor.get("state") != "observed"
                or cursor.get("validated") is not False
                or cursor.get("role") != "cursor"
                or cursor.get("topics") != []
                or cursor.get("complete") is not True
                or cursor.get("truncated") is not False
                or adapter_id != nonempty_text(scope.get("adapter_id"))
                or repository
                != nonempty_text(scope.get("canonical_repository"))
                or cursor.get("repo_name") != scope.get("repo_name")):
            raise ValueError("cursor report does not match its repository scope")
        current = self.latest_cursor(adapter_id, repository)
        if current is not None and after_revision != current["revision"]:
            raise ValueError(
                "cursor report does not advance from the current repository cursor")

    @staticmethod
    def _source_records(report: dict) -> list[dict]:
        for key in ("source_records", "sources"):
            value = report.get(key)
            if isinstance(value, list):
                return value
        return []

    def _ingest_observation(
            self, run_id: str, item: dict, observed_at: str,
    ) -> tuple[int, int, str, tuple[str, str, str | None]]:
        if not isinstance(item, dict):
            raise ValueError("source observation must be an object")
        source_id = nonempty_text(item.get("id") or item.get("source_id"))
        authority = nonempty_text(item.get("authority"))
        url = nonempty_text(item.get("url") or item.get("source_url"))
        state = nonempty_text(item.get("state"))
        if not all((source_id, authority, url, state)):
            raise ValueError("source observation lacks provenance")
        revision = nonempty_text(
            item.get("revision") or (item.get("observed") or {}).get("revision")
            or (item.get("observed") or {}).get("sha256"))
        validated = bool(
            item.get("validated") is True
            or (state == "current"
                and (item.get("observed") or {}).get("ok") is True))
        topics = normalized_topics(item.get("topics"))
        adapter_id = nonempty_text(item.get("adapter_id"))
        canonical_repository = nonempty_text(
            item.get("canonical_repository"))
        package_atom = nonempty_text(
            item.get("package_atom") or item.get("package"))
        payload_hash = sha256_json(item)
        fingerprint = sha256_json({
            "source_id": source_id,
            "authority": authority,
            "url": url,
            "revision": revision,
            "validated": validated,
            "topics": topics,
            "adapter_id": adapter_id,
            "canonical_repository": canonical_repository,
            "package_atom": package_atom,
            "payload_sha256": payload_hash,
        })
        cursor = self.connection.execute("""
            INSERT OR IGNORE INTO observations(
                fingerprint, source_id, authority, url, revision, state,
                validated, topics_json, adapter_id, canonical_repository,
                package_atom, observed_at, payload_sha256, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (fingerprint, source_id, authority, url, revision, state,
              int(validated), canonical_json(topics), adapter_id,
              canonical_repository, package_atom, observed_at, payload_hash,
              canonical_json(item)))
        link = self.connection.execute("""
            INSERT OR IGNORE INTO run_observations(run_id, fingerprint)
            VALUES (?, ?)
        """, (run_id, fingerprint))
        return (int(cursor.rowcount == 1), int(link.rowcount == 1), fingerprint,
                (source_id, url, revision))

    def _ingest_candidate(
            self, run_id: str, item: dict, observed_at: str,
            observation_index: dict[tuple[str, str, str | None], str],
    ) -> tuple[int, int, int]:
        if not isinstance(item, dict):
            raise ValueError("candidate must be an object")
        topic = nonempty_text(item.get("topic"))
        scope = nonempty_text(item.get("scope"))
        authority = nonempty_text(item.get("authority"))
        source_id = nonempty_text(item.get("source_id"))
        source_url = nonempty_text(item.get("source_url"))
        source_revision = nonempty_text(item.get("source_revision"))
        if not all((topic, scope, authority, source_id, source_url)):
            raise ValueError("candidate lacks topic, scope, or provenance")
        key = (source_id, source_url, source_revision)
        source_fingerprint = observation_index.get(key)
        if source_fingerprint is None:
            raise ValueError(
                "candidate source is not an independently collected observation")
        source_observation = self.connection.execute(
            "SELECT * FROM observations WHERE fingerprint = ?",
            (source_fingerprint,)).fetchone()
        source_payload = json.loads(source_observation["payload_json"])
        if (authority != "candidate-history"
                or source_observation["authority"] != authority
                or source_id != f"candidate-history:{source_revision}"
                or item.get("policy_status") != "not-established"
                or source_payload.get("source_id") != source_id
                or source_payload.get("role") != "candidate"
                or source_payload.get("state") != "observed"
                or source_payload.get("validated") is not False
                or source_payload.get("adapter_id") != item.get("adapter_id")
                or source_payload.get("canonical_repository") != scope
                or topic not in source_payload.get("topics", [])):
            raise ValueError(
                "candidate does not match a collected candidate-history observation")
        payload_hash = sha256_json(item)
        candidate_key = nonempty_text(item.get("candidate_key")) or sha256_json({
            "topic": topic,
            "scope": scope,
            "source_fingerprint": source_fingerprint,
            "payload_sha256": payload_hash,
        })
        existing = self.connection.execute(
            "SELECT * FROM candidates WHERE candidate_key = ?",
            (candidate_key,)).fetchone()
        if existing:
            if (existing["payload_sha256"] != payload_hash
                    or existing["source_fingerprint"] != source_fingerprint):
                raise ValueError(
                    "candidate key collision with different evidence or content")
            return 0, 0, 0
        self.connection.execute("""
            INSERT INTO candidates(
                candidate_key, topic, scope, payload_sha256, payload_json,
                state, discovered_run_id, source_fingerprint,
                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
        """, (candidate_key, topic, scope, payload_hash, canonical_json(item),
              run_id, source_fingerprint, observed_at, observed_at))
        return 1, 0, 0

    def list_candidates(self, state: str | None = None) -> list[dict]:
        query = "SELECT * FROM candidates"
        parameters: tuple[str, ...] = ()
        if state:
            if state not in TRANSITIONS:
                raise ValueError(f"unknown candidate state: {state}")
            query += " WHERE state = ?"
            parameters = (state,)
        query += " ORDER BY created_at, candidate_key"
        return [dict(row) for row in self.connection.execute(query, parameters)]

    def list_observations(self, source_id: str | None = None) -> list[dict]:
        query = "SELECT * FROM observations"
        parameters: tuple[str, ...] = ()
        if source_id:
            query += " WHERE source_id = ?"
            parameters = (source_id,)
        query += " ORDER BY observed_at, fingerprint"
        return [dict(row) for row in self.connection.execute(query, parameters)]

    def compact(self, keep_runs: int = 32) -> dict:
        if not isinstance(keep_runs, int) or not 1 <= keep_runs <= 1000:
            raise ValueError("evidence retention must keep between 1 and 1000 runs")
        all_runs = self.connection.execute(
            "SELECT run_id FROM runs ORDER BY rowid DESC").fetchall()
        protected = {row["run_id"] for row in all_runs[:keep_runs]}
        protected.update(row["discovered_run_id"] for row in
                         self.connection.execute(
                             "SELECT discovered_run_id FROM candidates"))

        protected_fingerprints = set()
        for columns, table in (
                (("source_fingerprint", "promotion_fingerprint"), "candidates"),
                (("fingerprint",), "candidate_evidence_links"),
                (("evidence_fingerprint",), "candidate_transitions")):
            for row in self.connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table}"):
                protected_fingerprints.update(
                    row[column] for column in columns if row[column])
        for fingerprint in protected_fingerprints:
            row = self.connection.execute(
                """
                SELECT runs.run_id
                FROM runs JOIN run_observations USING (run_id)
                WHERE fingerprint = ? AND runs.status = 'passed'
                ORDER BY runs.rowid DESC LIMIT 1
                """, (fingerprint,)).fetchone()
            if row:
                protected.add(row["run_id"])

        seen_cursors: set[tuple[str, str]] = set()
        for row in self.connection.execute(
                """
                SELECT runs.run_id, observations.adapter_id,
                    observations.canonical_repository
                FROM observations
                JOIN run_observations USING (fingerprint)
                JOIN runs USING (run_id)
                WHERE observations.source_id = 'scope-cursor'
                    AND observations.authority = 'repository-cursor'
                    AND runs.status = 'passed'
                ORDER BY runs.rowid DESC
                """):
            identity = (row["adapter_id"], row["canonical_repository"])
            if identity not in seen_cursors:
                seen_cursors.add(identity)
                protected.add(row["run_id"])

        removed_run_ids = [row["run_id"] for row in all_runs
                           if row["run_id"] not in protected]
        recent_run_ids = {row["run_id"] for row in all_runs[:keep_runs]}
        compacted_runs = 0
        with self.connection:
            if removed_run_ids:
                placeholders = ",".join("?" for _ in removed_run_ids)
                self.connection.execute(
                    f"DELETE FROM run_observations WHERE run_id IN ({placeholders})",
                    removed_run_ids)
                self.connection.execute(
                    f"DELETE FROM runs WHERE run_id IN ({placeholders})",
                    removed_run_ids)
            for run_id in protected - recent_run_ids:
                row = self.connection.execute(
                    "SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
                if row is None:
                    continue
                try:
                    current = json.loads(row["report_json"])
                except json.JSONDecodeError:
                    current = {}
                if current.get("compacted") is True:
                    continue
                summary = canonical_json({
                    "schema": 1,
                    "compacted": True,
                    "original_report_sha256": row["report_sha256"],
                    "kind": row["kind"],
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                })
                self.connection.execute(
                    "UPDATE runs SET report_json = ? WHERE run_id = ?",
                    (summary, run_id))
                compacted_runs += 1
            observations_before = self.connection.execute(
                "SELECT COUNT(*) AS count FROM observations").fetchone()["count"]
            self.connection.execute(
                """
                DELETE FROM observations
                WHERE NOT EXISTS (
                    SELECT 1 FROM run_observations
                    WHERE run_observations.fingerprint = observations.fingerprint)
                  AND NOT EXISTS (
                    SELECT 1 FROM candidates
                    WHERE candidates.source_fingerprint = observations.fingerprint
                       OR candidates.promotion_fingerprint = observations.fingerprint)
                  AND NOT EXISTS (
                    SELECT 1 FROM candidate_evidence_links
                    WHERE candidate_evidence_links.fingerprint = observations.fingerprint)
                  AND NOT EXISTS (
                    SELECT 1 FROM candidate_transitions
                    WHERE candidate_transitions.evidence_fingerprint = observations.fingerprint)
                """)
            observations_after = self.connection.execute(
                "SELECT COUNT(*) AS count FROM observations").fetchone()["count"]
        if removed_run_ids or compacted_runs:
            self.connection.execute("VACUUM")
        self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database_bytes = self.path.stat().st_size
        if database_bytes > MAX_STATE_BYTES:
            raise ValueError(
                "compacted evidence database exceeds the state size ceiling")
        return {
            "schema": SCHEMA_VERSION,
            "runs_before": len(all_runs),
            "runs_kept": len(all_runs) - len(removed_run_ids),
            "runs_removed": len(removed_run_ids),
            "runs_compacted": compacted_runs,
            "observations_removed": observations_before - observations_after,
            "protected_candidate_observations": len(protected_fingerprints),
            "protected_cursor_scopes": len(seen_cursors),
            "database_bytes": database_bytes,
            "maximum_database_bytes": MAX_STATE_BYTES,
        }

    def latest_cursor(self, adapter_id: str,
                      canonical_repository: str) -> dict | None:
        adapter_id = nonempty_text(adapter_id)
        canonical_repository = nonempty_text(canonical_repository)
        if not adapter_id or not canonical_repository:
            raise ValueError(
                "adapter ID and canonical repository are required")
        row = self.connection.execute(
            """
            SELECT observations.*, runs.run_id, runs.completed_at
            FROM observations
            JOIN run_observations USING (fingerprint)
            JOIN runs USING (run_id)
            WHERE source_id = 'scope-cursor'
                AND authority = 'repository-cursor'
                AND adapter_id = ?
                AND canonical_repository = ?
                AND runs.status = 'passed'
            ORDER BY runs.rowid DESC, runs.completed_at DESC,
                observations.observed_at DESC,
                observations.fingerprint DESC
            LIMIT 1
            """, (adapter_id, canonical_repository)).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])
        if (payload.get("complete") is not True
                or payload.get("truncated") is not False
                or not IMMUTABLE_REVISION_RE.fullmatch(row["revision"] or "")):
            raise ValueError("stored repository cursor is incomplete or mutable")
        return dict(row)

    def link_reviewed_evidence(self, candidate_key: str,
                               evidence_fingerprint: str,
                               reason: str) -> dict:
        reason = reason.strip()
        if not reason:
            raise ValueError("a reviewed-evidence link reason is required")
        with self.connection:
            candidate = self.connection.execute(
                "SELECT state FROM candidates WHERE candidate_key = ?",
                (candidate_key,)).fetchone()
            if candidate is None:
                raise ValueError(f"unknown candidate: {candidate_key}")
            if candidate["state"] != "reviewed":
                raise ValueError(
                    "reviewed evidence can be linked only to a reviewed candidate")
            observation = self.connection.execute(
                """
                SELECT 1
                FROM run_observations
                JOIN runs USING (run_id)
                WHERE fingerprint = ? AND runs.status = 'passed'
                LIMIT 1
                """, (evidence_fingerprint,)).fetchone()
            if observation is None:
                raise ValueError(
                    "reviewed evidence is not stored by a passed run")
            existing = self.connection.execute(
                """
                SELECT review_reason, linked_at
                FROM candidate_evidence_links
                WHERE candidate_key = ? AND fingerprint = ?
                """, (candidate_key, evidence_fingerprint)).fetchone()
            if existing:
                if existing["review_reason"] != reason:
                    raise ValueError(
                        "reviewed-evidence link already exists with another reason")
                return {
                    "candidate_key": candidate_key,
                    "fingerprint": evidence_fingerprint,
                    **dict(existing),
                }
            linked_at = utc_now()
            self.connection.execute(
                """
                INSERT INTO candidate_evidence_links(
                    candidate_key, fingerprint, review_reason, linked_at)
                VALUES (?, ?, ?, ?)
                """, (candidate_key, evidence_fingerprint, reason, linked_at))
        return {
            "candidate_key": candidate_key,
            "fingerprint": evidence_fingerprint,
            "review_reason": reason,
            "linked_at": linked_at,
        }

    @staticmethod
    def _validate_promotion_scope(candidate: sqlite3.Row,
                                  observation: sqlite3.Row,
                                  checklist: dict) -> None:
        claim_type = nonempty_text(checklist.get("claim_type"))
        if claim_type not in set().union(*AUTHORITY_CLAIM_TYPES.values()):
            raise ValueError(f"unknown promotion claim type: {claim_type}")
        authority = observation["authority"]
        if claim_type not in AUTHORITY_CLAIM_TYPES.get(authority, set()):
            raise ValueError(
                "promotion evidence authority is not eligible for the claim type")
        topics = json.loads(observation["topics_json"])
        if candidate["topic"] not in topics:
            raise ValueError(
                "promotion evidence topics do not include the candidate topic")

        payload = json.loads(candidate["payload_json"])
        if claim_type == "repository-policy":
            candidate_adapter = nonempty_text(payload.get("adapter_id"))
            candidate_repository = nonempty_text(
                payload.get("canonical_repository") or candidate["scope"])
            if (not candidate_adapter or not candidate_repository
                    or observation["adapter_id"] != candidate_adapter
                    or observation["canonical_repository"]
                    != candidate_repository):
                raise ValueError(
                    "overlay-policy evidence does not match the candidate adapter "
                    "and canonical repository")
        elif claim_type in {
                "package-upstream-fact", "package-gentoo-semantics"}:
            candidate_package = nonempty_text(
                payload.get("package_atom") or payload.get("package"))
            if not candidate_package:
                raise ValueError(
                    "package promotion requires a candidate package atom")
            if (claim_type == "package-upstream-fact"
                    and observation["package_atom"] != candidate_package):
                raise ValueError(
                    "upstream-primary evidence does not match the candidate package")
        elif claim_type == "skill-architecture":
            if candidate["scope"] != SKILLS_REPOSITORY:
                raise ValueError(
                    "skill-architecture evidence is limited to this repository")

    def transition(self, candidate_key: str, expected_state: str,
                   new_state: str, reason: str, *,
                   evidence_fingerprint: str | None = None,
                   checklist: dict | None = None) -> dict:
        reason = reason.strip()
        if not reason:
            raise ValueError("a transition reason is required")
        if new_state not in TRANSITIONS.get(expected_state, set()):
            raise ValueError(f"invalid transition: {expected_state} -> {new_state}")
        checklist_json = None
        with self.connection:
            row = self.connection.execute(
                "SELECT * FROM candidates WHERE candidate_key = ?",
                (candidate_key,)).fetchone()
            if row is None:
                raise ValueError(f"unknown candidate: {candidate_key}")
            if row["state"] != expected_state:
                raise ValueError(
                    f"candidate state changed: expected {expected_state}, "
                    f"found {row['state']}")
            if new_state == "promoted":
                checklist_json = validate_checklist(checklist)
                if not evidence_fingerprint:
                    raise ValueError(
                        "promotion requires a stored primary observation")
                observation = self.connection.execute(
                    "SELECT * FROM observations WHERE fingerprint = ?",
                    (evidence_fingerprint,)).fetchone()
                if observation is None:
                    raise ValueError("promotion evidence is not stored")
                passed_run = self.connection.execute(
                    """
                    SELECT 1
                    FROM run_observations
                    JOIN runs USING (run_id)
                    WHERE fingerprint = ? AND runs.status = 'passed'
                    LIMIT 1
                    """, (evidence_fingerprint,)).fetchone()
                if passed_run is None:
                    raise ValueError(
                        "promotion evidence is not stored by a passed run")
                reviewed_link = self.connection.execute(
                    """
                    SELECT 1 FROM candidate_evidence_links
                    WHERE candidate_key = ? AND fingerprint = ?
                    """, (candidate_key, evidence_fingerprint)).fetchone()
                if reviewed_link is None:
                    raise ValueError(
                        "promotion evidence is not linked by explicit review")
                if observation["authority"] not in PRIMARY_AUTHORITIES:
                    raise ValueError(
                        "promotion evidence is not a primary authority")
                if (not observation["url"].startswith("https://")
                        or not IMMUTABLE_REVISION_RE.fullmatch(
                            observation["revision"] or "")
                        or observation["validated"] != 1
                        or observation["state"] not in {"current", "validated"}):
                    raise ValueError(
                        "promotion evidence lacks a usable immutable revision")
                if checklist["scope"] != row["scope"]:
                    raise ValueError(
                        "promotion checklist scope does not match the candidate")
                self._validate_promotion_scope(row, observation, checklist)
            changed_at = utc_now()
            cursor = self.connection.execute("""
                UPDATE candidates
                SET state = ?, reason = ?, promotion_fingerprint = ?,
                    checklist_json = ?, updated_at = ?
                WHERE candidate_key = ? AND state = ?
            """, (new_state, reason, evidence_fingerprint, checklist_json,
                  changed_at, candidate_key, expected_state))
            if cursor.rowcount != 1:
                raise RuntimeError("candidate compare-and-swap failed")
            self.connection.execute("""
                INSERT INTO candidate_transitions(
                    candidate_key, old_state, new_state, reason,
                    evidence_fingerprint, checklist_json, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (candidate_key, expected_state, new_state, reason,
                  evidence_fingerprint, checklist_json, changed_at))
        return dict(self.connection.execute(
            "SELECT * FROM candidates WHERE candidate_key = ?",
            (candidate_key,)).fetchone())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db", required=True, type=Path,
                        help="SQLite evidence database path")
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="initialize and validate the database")
    ingest = subparsers.add_parser("ingest", help="ingest one JSON report")
    ingest.add_argument("report", type=Path)
    ingest.add_argument("--kind", required=True)
    listing = subparsers.add_parser("list", help="list evidence candidates")
    listing.add_argument("--state", choices=sorted(TRANSITIONS))
    observations = subparsers.add_parser(
        "observations", help="list stored source observations")
    observations.add_argument("--source-id")
    cursor = subparsers.add_parser(
        "latest-cursor", help="show the latest complete repository cursor")
    cursor.add_argument("--adapter-id", required=True)
    cursor.add_argument("--canonical-repository", required=True)
    transition = subparsers.add_parser(
        "transition", help="apply a reviewed candidate state transition")
    transition.add_argument("candidate_key")
    transition.add_argument("--from-state", required=True,
                            choices=sorted(TRANSITIONS))
    transition.add_argument("--to-state", required=True,
                            choices=sorted(TRANSITIONS))
    transition.add_argument("--reason", required=True)
    transition.add_argument("--evidence-fingerprint")
    transition.add_argument("--checklist", type=Path)
    link = subparsers.add_parser(
        "link-evidence", help="link stored evidence reviewed for one candidate")
    link.add_argument("candidate_key")
    link.add_argument("evidence_fingerprint")
    link.add_argument("--reason", required=True)
    compact = subparsers.add_parser(
        "compact", help="remove old routine runs while preserving review evidence")
    compact.add_argument("--keep-runs", type=int, default=32)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        with EvidenceStore(args.db) as store:
            if args.command == "init":
                output = {"schema": SCHEMA_VERSION, "database": str(store.path)}
            elif args.command == "ingest":
                report = json.loads(args.report.read_text(encoding="utf-8"))
                output = store.ingest(report, args.kind)
            elif args.command == "list":
                output = store.list_candidates(args.state)
            elif args.command == "observations":
                output = store.list_observations(args.source_id)
            elif args.command == "latest-cursor":
                output = store.latest_cursor(
                    args.adapter_id, args.canonical_repository)
            elif args.command == "transition":
                checklist = (json.loads(args.checklist.read_text(encoding="utf-8"))
                             if args.checklist else None)
                output = store.transition(
                    args.candidate_key, args.from_state, args.to_state,
                    args.reason, evidence_fingerprint=args.evidence_fingerprint,
                    checklist=checklist)
            elif args.command == "link-evidence":
                output = store.link_reviewed_evidence(
                    args.candidate_key, args.evidence_fingerprint, args.reason)
            else:
                output = store.compact(args.keep_runs)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
