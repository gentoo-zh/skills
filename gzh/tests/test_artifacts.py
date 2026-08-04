import hashlib
import json

import pytest

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


def test_manifest_parser_rejects_duplicate_dist(tmp_path):
    line = "DIST demo.tar.gz 1 BLAKE2B aa SHA512 bb\n"
    with pytest.raises(ArtifactError, match="duplicate"):
        parse_manifest_artifacts(line + line)


def test_unused_evidence_is_reported(tmp_path):
    manifest = _manifest(tmp_path)
    evidence = tmp_path / "artifacts.json"
    evidence.write_text(json.dumps({"artifacts": [
        {"filename": "demo.tar.gz", "source_url": "https://example.invalid/demo"},
        {"filename": "old.tar.gz", "source_url": "https://example.invalid/old"},
    ]}), encoding="utf-8")
    report = audit_artifacts(manifest, evidence=evidence)
    assert report["ok"] is True
    assert report["findings"][0]["code"] == "unused-source-evidence"
