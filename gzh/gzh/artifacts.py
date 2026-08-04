from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


HASH_NAMES = {"BLAKE2B": "blake2b", "SHA512": "sha512"}


class ArtifactError(ValueError):
    pass


def parse_manifest_artifacts(text: str) -> list[dict]:
    artifacts: list[dict] = []
    seen: set[str] = set()
    for number, line in enumerate((text or "").splitlines(), start=1):
        parts = line.split()
        if not parts or parts[0] != "DIST":
            continue
        if len(parts) < 7 or not parts[2].isdigit() or len(parts[3:]) % 2:
            raise ArtifactError(f"invalid DIST entry on Manifest line {number}")
        name = parts[1]
        if name in seen:
            raise ArtifactError(f"duplicate DIST entry: {name}")
        seen.add(name)
        hashes: dict[str, str] = {}
        for index in range(3, len(parts), 2):
            algorithm, digest = parts[index], parts[index + 1]
            if algorithm in hashes:
                raise ArtifactError(f"duplicate {algorithm} digest for {name}")
            hashes[algorithm] = digest.lower()
        artifacts.append({"hashes": hashes, "name": name, "size": int(parts[2])})
    return artifacts


def load_artifact_evidence(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArtifactError(f"cannot read artifact evidence: {exc}") from exc
    records = document.get("artifacts") if isinstance(document, dict) else None
    if not isinstance(records, list):
        raise ArtifactError("artifact evidence must contain an artifacts array")
    result: dict[str, dict] = {}
    allowed = {
        "architecture", "digest", "filename", "release_url", "signature_url",
        "size", "source_url",
    }
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("filename"), str):
            raise ArtifactError("each artifact evidence record needs a filename")
        unknown = sorted(set(record) - allowed)
        if unknown:
            raise ArtifactError(f"unknown artifact evidence fields: {', '.join(unknown)}")
        name = record["filename"]
        if Path(name).name != name or name in result:
            raise ArtifactError(f"invalid or duplicate artifact evidence filename: {name}")
        for field in ("source_url", "release_url", "signature_url", "architecture", "digest"):
            if field in record and not isinstance(record[field], str):
                raise ArtifactError(f"{field} must be a string for {name}")
        if "size" in record and (isinstance(record["size"], bool)
                                 or not isinstance(record["size"], int)
                                 or record["size"] < 0):
            raise ArtifactError(f"size must be a non-negative integer for {name}")
        result[name] = dict(record)
    return result


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def audit_artifacts(
    manifest: Path,
    *,
    evidence: Path | None = None,
    distdir: Path | None = None,
) -> dict:
    manifest = Path(manifest).resolve()
    entries = parse_manifest_artifacts(manifest.read_text(encoding="utf-8"))
    evidence_map = load_artifact_evidence(evidence)
    distdir = Path(distdir).resolve() if distdir else None
    reports: list[dict[str, Any]] = []
    findings: list[dict] = []
    manifest_names = {entry["name"] for entry in entries}

    for entry in entries:
        name = entry["name"]
        source = evidence_map.get(name)
        record: dict[str, Any] = {**entry, "evidence": source, "local": None}
        if source is None:
            findings.append({
                "code": "missing-source-evidence",
                "message": "DIST entry has no reviewed source mapping",
                "name": name,
                "severity": "error",
            })
        elif not source.get("source_url") and not source.get("release_url"):
            findings.append({
                "code": "missing-source-url",
                "message": "artifact evidence has neither source_url nor release_url",
                "name": name,
                "severity": "error",
            })
        if source is not None and "size" in source and source["size"] != entry["size"]:
            findings.append({
                "code": "evidence-size-mismatch",
                "expected": source["size"],
                "manifest": entry["size"],
                "message": "reviewed source size differs from the Manifest",
                "name": name,
                "severity": "error",
            })

        if distdir is not None:
            local = distdir / name
            if not local.is_file():
                findings.append({
                    "code": "missing-distfile",
                    "message": "DIST file is absent from the selected distdir",
                    "name": name,
                    "severity": "error",
                })
            else:
                local_hashes = {
                    label: _digest(local, algorithm)
                    for label, algorithm in HASH_NAMES.items()
                    if label in entry["hashes"]
                }
                record["local"] = {
                    "hashes": local_hashes,
                    "path": str(local),
                    "size": local.stat().st_size,
                }
                if local.stat().st_size != entry["size"]:
                    findings.append({
                        "code": "distfile-size-mismatch",
                        "message": "local DIST file size differs from the Manifest",
                        "name": name,
                        "severity": "error",
                    })
                for label, observed in local_hashes.items():
                    if observed != entry["hashes"][label]:
                        findings.append({
                            "code": "distfile-digest-mismatch",
                            "algorithm": label,
                            "message": "local DIST file digest differs from the Manifest",
                            "name": name,
                            "severity": "error",
                        })
        reports.append(record)

    extras = sorted(set(evidence_map) - manifest_names)
    for name in extras:
        findings.append({
            "code": "unused-source-evidence",
            "message": "source evidence does not match a DIST entry",
            "name": name,
            "severity": "warning",
        })
    complete = bool(entries) and all(report["evidence"] is not None for report in reports)
    if distdir is not None:
        complete = complete and all(report["local"] is not None for report in reports)
    return {
        "artifacts": reports,
        "complete": complete,
        "distdir": str(distdir) if distdir else None,
        "evidence": str(Path(evidence).resolve()) if evidence else None,
        "findings": findings,
        "limitations": [
            "Manifest digest agreement does not establish upstream artifact provenance.",
            "License and redistribution decisions require separate reviewed evidence.",
        ],
        "manifest": str(manifest),
        "ok": complete and not any(item["severity"] == "error" for item in findings),
        "provenance_established": False,
        "truncated": False,
    }
