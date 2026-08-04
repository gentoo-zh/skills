#!/usr/bin/env python3
"""Create and verify an integrity manifest for maintenance SQLite state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


SCHEMA_VERSION = 1
IMMUTABLE_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
ALLOWED_EVENTS = {"schedule", "workflow_dispatch"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_write(path: Path, content: str) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def regular_file(path: Path) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink() or not expanded.is_file():
        raise ValueError(f"state database is not a regular file: {expanded}")
    return expanded.resolve()


def state_sidecars(path: Path) -> list[Path]:
    return [Path(f"{path}{suffix}") for suffix in ("-wal", "-shm")]


def database_integrity(path: Path, *, checkpoint: bool) -> str:
    path = regular_file(path)
    if checkpoint:
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA journal_mode = WAL").fetchone()
            checkpoint_result = connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint_result != (0, 0, 0):
                raise ValueError(
                    f"SQLite WAL checkpoint was incomplete: {checkpoint_result}")
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
        nonempty = [sidecar for sidecar in state_sidecars(path)
                    if sidecar.exists() and sidecar.stat().st_size > 0]
        if nonempty:
            raise ValueError(
                "SQLite state has nonempty sidecars after checkpoint: "
                + ", ".join(str(sidecar) for sidecar in nonempty))
    else:
        uri = f"file:{path.as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
    if result != "ok":
        raise ValueError(f"SQLite quick_check failed: {result}")
    return result


def file_record(path: Path) -> dict:
    content = path.read_bytes()
    return {
        "name": path.name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def create_manifest(database: Path, metadata: dict | None = None) -> dict:
    database = regular_file(database)
    database_integrity(database, checkpoint=True)
    return {
        "schema": SCHEMA_VERSION,
        "created_at": utc_now(),
        "database": file_record(database),
        "sqlite_quick_check": "ok",
        "metadata": metadata or {},
    }


def verify_manifest(database: Path, manifest: dict) -> dict:
    database = regular_file(database)
    sidecars = [path for path in state_sidecars(database) if path.exists()]
    if sidecars:
        raise ValueError(
            "restored SQLite state contains sidecars: "
            + ", ".join(str(path) for path in sidecars))
    if manifest.get("schema") != SCHEMA_VERSION:
        raise ValueError("unsupported state manifest schema")
    expected = manifest.get("database")
    if not isinstance(expected, dict):
        raise ValueError("state manifest has no database record")
    observed = file_record(database)
    for field in ("name", "bytes", "sha256"):
        if observed[field] != expected.get(field):
            raise ValueError(f"state database {field} does not match manifest")
    database_integrity(database, checkpoint=False)
    return {
        "schema": SCHEMA_VERSION,
        "verified": True,
        "database": observed,
        "manifest_created_at": manifest.get("created_at"),
        "metadata": manifest.get("metadata", {}),
    }


def verify_provenance(manifest: dict, run: dict, job: dict, *, repository: str,
                      workflow: str, branch: str) -> dict:
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("state manifest metadata is missing")
    run_repository = (run.get("repository") or {}).get("full_name")
    observed = {
        "repository": run_repository,
        "workflow": run.get("path"),
        "branch": run.get("head_branch"),
        "status": run.get("status"),
        "conclusion": run.get("conclusion"),
        "event": run.get("event"),
    }
    expected = {
        "repository": repository,
        "workflow": workflow,
        "branch": branch,
        "status": "completed",
    }
    for field, value in expected.items():
        if observed[field] != value:
            raise ValueError(
                f"workflow run {field} does not match: {observed[field]!r}")
    if observed["event"] not in ALLOWED_EVENTS:
        raise ValueError("workflow run event is not an allowed maintenance trigger")
    if observed["conclusion"] not in {"success", "failure"}:
        raise ValueError(
            "workflow run conclusion does not match an allowed final state")
    run_id = run.get("id")
    head_sha = run.get("head_sha")
    if not isinstance(run_id, int) or run_id <= 0:
        raise ValueError("workflow run ID is invalid")
    if not isinstance(head_sha, str) or not IMMUTABLE_REVISION_RE.fullmatch(
            head_sha):
        raise ValueError("workflow run head SHA is invalid")
    if (job.get("status") != "completed"
            or job.get("head_sha") != head_sha):
        raise ValueError("workflow job does not match the completed run")
    steps = {step.get("name"): step.get("conclusion")
             for step in job.get("steps", []) if isinstance(step, dict)}
    for name in (
            "Seal maintenance state",
            "Preserve authenticated maintenance state"):
        if steps.get(name) != "success":
            raise ValueError(f"workflow job did not complete {name}")
    expected_metadata = {
        "repository": repository,
        "workflow": workflow,
        "default_branch": branch,
        "run_id": run_id,
        "head_sha": head_sha,
        "event": observed["event"],
    }
    for field, value in expected_metadata.items():
        if metadata.get(field) != value:
            raise ValueError(
                f"state manifest {field} does not match the authenticated run")
    return {
        "schema": SCHEMA_VERSION,
        "verified": True,
        **expected_metadata,
    }


def restore_decision(*, restored: bool, prior_runs: int, event: str,
                     reviewed_seed: str | None = None,
                     stored_cursor: str | None = None) -> dict:
    if not isinstance(prior_runs, int) or prior_runs < 0:
        raise ValueError("prior workflow run count must be a nonnegative integer")
    if event not in ALLOWED_EVENTS:
        raise ValueError("state restoration event is not an allowed trigger")
    seed = (reviewed_seed or "").strip()
    cursor = (stored_cursor or "").strip()
    if seed and (event != "workflow_dispatch"
                 or not IMMUTABLE_REVISION_RE.fullmatch(seed)):
        raise ValueError(
            "an explicit reviewed seed must be a lowercase immutable commit ID "
            "from workflow_dispatch")
    if cursor and not IMMUTABLE_REVISION_RE.fullmatch(cursor):
        raise ValueError("stored cursor is not an immutable commit ID")
    if not restored and cursor:
        raise ValueError("a cursor cannot be restored without authenticated state")
    if restored and cursor:
        if seed:
            raise ValueError(
                "an explicit seed cannot replace an established repository cursor")
        return {
            "schema": SCHEMA_VERSION,
            "mode": "restored",
            "initialize": False,
            "cursor": cursor,
        }
    if seed:
        if restored:
            mode = "reviewed-cursor-recovery"
        elif prior_runs:
            mode = "reviewed-state-recovery"
        else:
            mode = "reviewed-initialization"
        return {
            "schema": SCHEMA_VERSION,
            "mode": mode,
            "initialize": not restored,
            "cursor": seed,
        }
    if restored:
        raise ValueError(
            "authenticated state has no repository cursor; an explicit reviewed "
            "seed is required")
    if prior_runs:
        raise ValueError(
            "historical maintenance state is missing; automatic cursor seeding is "
            "forbidden")
    raise ValueError(
        "first initialization requires an explicit reviewed cursor seed")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--database", required=True, type=Path)
    create.add_argument("--manifest", required=True, type=Path)
    create.add_argument("--metadata", type=Path,
                        help="optional JSON metadata to preserve in the manifest")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--database", required=True, type=Path)
    verify.add_argument("--manifest", required=True, type=Path)
    provenance = subparsers.add_parser("verify-provenance")
    provenance.add_argument("--manifest", required=True, type=Path)
    provenance.add_argument("--run-record", required=True, type=Path)
    provenance.add_argument("--job-record", required=True, type=Path)
    provenance.add_argument("--repository", required=True)
    provenance.add_argument("--workflow", required=True)
    provenance.add_argument("--branch", required=True)
    decision = subparsers.add_parser("restore-decision")
    decision.add_argument("--restored", action="store_true")
    decision.add_argument("--prior-runs", required=True, type=int)
    decision.add_argument("--event", required=True)
    decision.add_argument("--reviewed-seed")
    decision.add_argument("--stored-cursor")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "create":
            metadata = (json.loads(args.metadata.read_text(encoding="utf-8"))
                        if args.metadata else None)
            if metadata is not None and not isinstance(metadata, dict):
                raise ValueError("state metadata must be a JSON object")
            output = create_manifest(args.database, metadata)
            atomic_write(
                args.manifest,
                json.dumps(output, ensure_ascii=False, indent=2) + "\n")
        elif args.command == "verify":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            output = verify_manifest(args.database, manifest)
        elif args.command == "verify-provenance":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            run = json.loads(args.run_record.read_text(encoding="utf-8"))
            job = json.loads(args.job_record.read_text(encoding="utf-8"))
            output = verify_provenance(
                manifest, run, job, repository=args.repository,
                workflow=args.workflow, branch=args.branch)
        else:
            output = restore_decision(
                restored=args.restored, prior_runs=args.prior_runs,
                event=args.event, reviewed_seed=args.reviewed_seed,
                stored_cursor=args.stored_cursor)
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
