import hashlib
import json
import os

import pytest

import gzh.artifacts as artifacts
from gzh.artifacts import ArtifactError, audit_artifacts, parse_manifest_artifacts


def _manifest(tmp_path, content=b"payload"):
    b2 = hashlib.blake2b(content).hexdigest()
    sha = hashlib.sha512(content).hexdigest()
    path = tmp_path / "Manifest"
    path.write_text(
        f"DIST demo.tar.gz {len(content)} BLAKE2B {b2} SHA512 {sha}\n",
        encoding="utf-8",
    )
    return path


def _evidence(tmp_path, size=7):
    path = tmp_path / "artifacts.json"
    path.write_text(json.dumps({"artifacts": [{
        "filename": "demo.tar.gz",
        "release_url": "https://example.invalid/releases/1",
        "source_url": "https://example.invalid/demo.tar.gz",
        "size": size,
        "inspection_available": True,
        "portage_fetch_state": "verified",
        "portage_fetch_evidence": "pkgdev manifest completed with default fetch settings",
    }]}), encoding="utf-8")
    return path


def test_artifact_audit_verifies_every_distfile(tmp_path):
    content = b"payload"
    manifest = _manifest(tmp_path, content)
    evidence = _evidence(tmp_path, len(content))
    distdir = tmp_path / "distfiles"
    distdir.mkdir()
    (distdir / "demo.tar.gz").write_bytes(content)

    report = audit_artifacts(manifest, evidence=evidence, distdir=distdir)

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["artifacts"][0]["local"]["size"] == len(content)
    assert report["artifacts"][0]["states"]["artifact_identity"] == (
        "manifest-digest-matched")
    assert report["provenance_established"] is False


def test_missing_evidence_is_incomplete(tmp_path):
    report = audit_artifacts(_manifest(tmp_path))
    assert report["ok"] is False
    assert report["complete"] is False
    assert report["findings"][0]["code"] == "missing-source-evidence"


def test_digest_mismatch_fails(tmp_path):
    manifest = _manifest(tmp_path)
    distdir = tmp_path / "distfiles"
    distdir.mkdir()
    (distdir / "demo.tar.gz").write_bytes(b"PAYLOAD")

    report = audit_artifacts(
        manifest,
        evidence=_evidence(tmp_path),
        distdir=distdir,
    )
    assert report["ok"] is False
    assert {item["code"] for item in report["findings"]} >= {
        "distfile-digest-mismatch"
    }
    assert report["artifacts"][0]["states"]["artifact_identity"] == (
        "local-mismatch")


def test_manifest_parser_rejects_duplicate_dist(tmp_path):
    line = f"DIST demo.tar.gz 1 BLAKE2B {'a' * 128} SHA512 {'b' * 128}\n"
    with pytest.raises(ArtifactError, match="duplicate"):
        parse_manifest_artifacts(line + line)


@pytest.mark.parametrize("name", ["../demo.tar.gz", "/tmp/demo.tar.gz", ".", ".."])
def test_manifest_parser_rejects_non_basename_dist_names(name):
    line = f"DIST {name} 1 BLAKE2B {'a' * 128} SHA512 {'b' * 128}\n"
    with pytest.raises(ArtifactError, match="basename"):
        parse_manifest_artifacts(line)


@pytest.mark.parametrize("hashes", [
    f"BLAKE2B {'a' * 127} SHA512 {'b' * 128}",
    f"BLAKE2B {'a' * 128} SHA512 not-hex",
    f"BLAKE2B {'a' * 128}",
    f"MD5 {'a' * 32} SHA512 {'b' * 128}",
])
def test_manifest_parser_rejects_missing_weak_or_unknown_digests(hashes):
    line = f"DIST demo.tar.gz 1 {hashes}\n"
    with pytest.raises(ArtifactError, match="digest|DIST entry"):
        parse_manifest_artifacts(line)


def test_artifact_audit_rejects_symlink_distfile(tmp_path):
    manifest = _manifest(tmp_path)
    evidence = _evidence(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"payload")
    distdir = tmp_path / "distfiles"
    distdir.mkdir()
    (distdir / "demo.tar.gz").symlink_to(outside)

    report = audit_artifacts(manifest, evidence=evidence, distdir=distdir)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["artifacts"][0]["local"] is None
    assert report["findings"][-1]["code"] == "nonregular-distfile"


def test_artifact_audit_rejects_nonregular_distfile(tmp_path):
    manifest = _manifest(tmp_path)
    distdir = tmp_path / "distfiles"
    distdir.mkdir()
    (distdir / "demo.tar.gz").mkdir()

    report = audit_artifacts(
        manifest, evidence=_evidence(tmp_path), distdir=distdir)

    assert report["ok"] is False
    assert report["findings"][-1]["code"] == "nonregular-distfile"


def test_artifact_audit_rejects_input_changed_during_hashing(tmp_path, monkeypatch):
    manifest = _manifest(tmp_path)
    distdir = tmp_path / "distfiles"
    distdir.mkdir()
    local = distdir / "demo.tar.gz"
    local.write_bytes(b"payload")
    original_hash_stream = artifacts._hash_stream

    def mutate_after_hash(handle):
        result = original_hash_stream(handle)
        local.write_bytes(b"changed")
        os.utime(local, None)
        return result

    monkeypatch.setattr(artifacts, "_hash_stream", mutate_after_hash)
    report = audit_artifacts(
        manifest, evidence=_evidence(tmp_path), distdir=distdir)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["artifacts"][0]["local"] is None
    assert report["findings"][-1]["code"] == "unstable-distfile"


def test_unused_evidence_is_reported(tmp_path):
    manifest = _manifest(tmp_path)
    evidence = tmp_path / "artifacts.json"
    evidence.write_text(json.dumps({"artifacts": [
        {"filename": "demo.tar.gz", "source_url": "https://example.invalid/demo",
         "inspection_available": True, "portage_fetch_state": "verified",
         "portage_fetch_evidence": "local fetch record"},
        {"filename": "old.tar.gz", "source_url": "https://example.invalid/old",
         "inspection_available": True, "portage_fetch_state": "verified",
         "portage_fetch_evidence": "local fetch record"},
    ]}), encoding="utf-8")
    report = audit_artifacts(manifest, evidence=evidence)
    assert report["ok"] is True
    assert report["findings"][0]["code"] == "unused-source-evidence"


def test_artifact_evidence_rejects_unverifiable_generic_digest(tmp_path):
    evidence = tmp_path / "artifacts.json"
    evidence.write_text(json.dumps({"artifacts": [{
        "filename": "demo.tar.gz",
        "source_url": "https://example.invalid/demo.tar.gz",
        "digest": "sha256:deadbeef",
        "inspection_available": True,
        "portage_fetch_state": "verified",
        "portage_fetch_evidence": "local fetch record",
    }]}), encoding="utf-8")

    with pytest.raises(ArtifactError, match="unknown artifact evidence fields: digest"):
        audit_artifacts(_manifest(tmp_path), evidence=evidence)


def test_manual_inspection_does_not_satisfy_failed_portage_fetch(tmp_path):
    evidence = tmp_path / "artifacts.json"
    evidence.write_text(json.dumps({"artifacts": [{
        "filename": "demo.tar.gz",
        "source_url": "https://example.invalid/demo.tar.gz",
        "inspection_available": True,
        "portage_fetch_state": "failed",
        "portage_fetch_evidence": "HTTP 403 from default fetch command",
    }]}), encoding="utf-8")

    report = audit_artifacts(_manifest(tmp_path), evidence=evidence)

    assert report["ok"] is False
    assert report["artifacts"][0]["states"]["inspection_available"] is True
    assert report["artifacts"][0]["states"]["portage_fetch"] == "failed"
    assert "portage-fetch-unverified" in {
        finding["code"] for finding in report["findings"]}


def test_authenticated_ci_can_supersede_local_fetch_failure(tmp_path):
    evidence = tmp_path / "artifacts.json"
    evidence.write_text(json.dumps({"artifacts": [{
        "filename": "demo.tar.gz",
        "source_url": "https://example.invalid/demo.tar.gz",
        "inspection_available": True,
        "portage_fetch_state": "superseded-by-ci",
        "portage_fetch_evidence": "https://github.example/run/123",
    }]}), encoding="utf-8")

    report = audit_artifacts(_manifest(tmp_path), evidence=evidence)

    assert report["ok"] is True
    assert report["artifacts"][0]["states"]["portage_fetch"] == (
        "superseded-by-ci")


def test_artifact_evidence_requires_separate_fetch_and_inspection_states(tmp_path):
    evidence = tmp_path / "artifacts.json"
    evidence.write_text(json.dumps({"artifacts": [{
        "filename": "demo.tar.gz",
        "source_url": "https://example.invalid/demo.tar.gz",
    }]}), encoding="utf-8")

    with pytest.raises(ArtifactError, match="inspection_available"):
        audit_artifacts(_manifest(tmp_path), evidence=evidence)
