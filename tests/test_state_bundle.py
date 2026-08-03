from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
PATH = (ROOT / ".agents" / "skills" / "gzh-maintain-skills"
        / "scripts" / "state_bundle.py")


def load_module():
    spec = importlib.util.spec_from_file_location("state_bundle_test", PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


bundle = load_module()


def provenance_fixture():
    metadata = {
        "repository": "example/skills",
        "workflow": ".github/workflows/reference-audit.yml",
        "default_branch": "master",
        "run_id": 123,
        "head_sha": "a" * 40,
        "event": "schedule",
    }
    manifest = {"schema": 1, "metadata": metadata}
    run = {
        "id": 123,
        "path": ".github/workflows/reference-audit.yml",
        "head_branch": "master",
        "head_sha": "a" * 40,
        "status": "completed",
        "conclusion": "failure",
        "event": "schedule",
        "repository": {"full_name": "example/skills"},
    }
    job = {
        "status": "completed",
        "conclusion": "failure",
        "head_sha": "a" * 40,
        "steps": [{
            "name": "Seal maintenance state",
            "conclusion": "success",
        }, {
            "name": "Preserve authenticated maintenance state",
            "conclusion": "success",
        }],
    }
    return manifest, run, job


def database(path: Path) -> Path:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
    connection.execute("INSERT INTO evidence VALUES ('complete')")
    connection.commit()
    connection.close()
    return path


def test_manifest_round_trip_verifies_hash_size_and_sqlite(tmp_path):
    path = database(tmp_path / "state.db")
    manifest = bundle.create_manifest(path, {"run_id": "123"})

    result = bundle.verify_manifest(path, manifest)

    assert result["verified"] is True
    assert result["database"]["sha256"] == manifest["database"]["sha256"]
    assert result["metadata"] == {"run_id": "123"}


def test_tampered_database_is_rejected_before_restore(tmp_path):
    path = database(tmp_path / "state.db")
    manifest = bundle.create_manifest(path)
    with path.open("ab") as handle:
        handle.write(b"tampered")

    with pytest.raises(ValueError, match="bytes does not match"):
        bundle.verify_manifest(path, manifest)


def test_manifest_with_wrong_hash_is_rejected(tmp_path):
    path = database(tmp_path / "state.db")
    manifest = bundle.create_manifest(path)
    manifest["database"]["sha256"] = "0" * 64

    with pytest.raises(ValueError, match="sha256 does not match"):
        bundle.verify_manifest(path, manifest)


def test_symlink_database_is_rejected(tmp_path):
    target = database(tmp_path / "target.db")
    link = tmp_path / "state.db"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="not a regular file"):
        bundle.create_manifest(link)


def test_non_sqlite_file_is_rejected(tmp_path):
    path = tmp_path / "state.db"
    path.write_text("not sqlite")

    with pytest.raises(sqlite3.DatabaseError):
        bundle.create_manifest(path)


def test_restored_sidecar_is_rejected_before_sqlite_open(tmp_path):
    path = database(tmp_path / "state.db")
    manifest = bundle.create_manifest(path)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA wal_autocheckpoint = 0")
    connection.execute("INSERT INTO evidence VALUES ('unmanifested')")
    connection.commit()
    try:
        assert Path(f"{path}-wal").exists()
        with pytest.raises(ValueError, match="contains sidecars"):
            bundle.verify_manifest(path, manifest)
    finally:
        connection.close()


def test_provenance_matches_authenticated_workflow_run():
    manifest, run, job = provenance_fixture()
    result = bundle.verify_provenance(
        manifest, run, job, repository="example/skills",
        workflow=".github/workflows/reference-audit.yml", branch="master")
    assert result["verified"] is True
    assert result["run_id"] == 123


@pytest.mark.parametrize(("field", "value"), [
    ("repository", {"full_name": "other/skills"}),
    ("path", ".github/workflows/other.yml"),
    ("head_branch", "feature"),
    ("status", "in_progress"),
    ("conclusion", "cancelled"),
    ("head_sha", "b" * 40),
])
def test_provenance_rejects_run_or_manifest_mismatch(field, value):
    manifest, run, job = provenance_fixture()
    run[field] = value
    with pytest.raises(ValueError, match="does not match"):
        bundle.verify_provenance(
            manifest, run, job, repository="example/skills",
            workflow=".github/workflows/reference-audit.yml", branch="master")


def test_provenance_rejects_manifest_identity_substitution():
    manifest, run, job = provenance_fixture()
    manifest["metadata"]["repository"] = "other/skills"
    with pytest.raises(ValueError, match="manifest repository"):
        bundle.verify_provenance(
            manifest, run, job, repository="example/skills",
            workflow=".github/workflows/reference-audit.yml", branch="master")


@pytest.mark.parametrize("step_name", [
    "Seal maintenance state",
    "Preserve authenticated maintenance state",
])
def test_provenance_rejects_failed_state_step(step_name):
    manifest, run, job = provenance_fixture()
    next(step for step in job["steps"]
         if step["name"] == step_name)["conclusion"] = "failure"
    with pytest.raises(ValueError, match=step_name):
        bundle.verify_provenance(
            manifest, run, job, repository="example/skills",
            workflow=".github/workflows/reference-audit.yml", branch="master")
