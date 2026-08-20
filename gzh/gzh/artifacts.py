from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any


HASH_NAMES = {"BLAKE2B": "blake2b", "SHA512": "sha512"}
_MANIFEST_DIGEST_RE = re.compile(r"[0-9a-fA-F]{128}")
PORTAGE_FETCH_STATES = {
    "verified", "failed", "not-tested", "superseded-by-ci"}


class ArtifactError(ValueError):
    pass


class _DistfileError(OSError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _valid_basename(name: str) -> bool:
    return (bool(name) and name not in {".", ".."}
            and not any(ord(character) < 32 for character in name)
            and Path(name).name == name)


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
        if not _valid_basename(name):
            raise ArtifactError(
                f"DIST name must be a basename on Manifest line {number}")
        if name in seen:
            raise ArtifactError(f"duplicate DIST entry: {name}")
        seen.add(name)
        hashes: dict[str, str] = {}
        for index in range(3, len(parts), 2):
            algorithm, digest = parts[index], parts[index + 1]
            if algorithm in hashes:
                raise ArtifactError(f"duplicate {algorithm} digest for {name}")
            hashes[algorithm] = digest.lower()
        if set(hashes) != set(HASH_NAMES):
            raise ArtifactError(
                f"DIST entry must contain only BLAKE2B and SHA512 digests: {name}")
        for algorithm in HASH_NAMES:
            if not _MANIFEST_DIGEST_RE.fullmatch(hashes[algorithm]):
                raise ArtifactError(
                    f"invalid {algorithm} digest for DIST entry: {name}")
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
        "architecture", "filename", "release_url", "signature_url",
        "size", "source_url", "inspection_available", "portage_fetch_state",
        "portage_fetch_evidence",
    }
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("filename"), str):
            raise ArtifactError("each artifact evidence record needs a filename")
        unknown = sorted(set(record) - allowed)
        if unknown:
            raise ArtifactError(f"unknown artifact evidence fields: {', '.join(unknown)}")
        name = record["filename"]
        if not _valid_basename(name) or name in result:
            raise ArtifactError(f"invalid or duplicate artifact evidence filename: {name}")
        for field in ("source_url", "release_url", "signature_url", "architecture"):
            if field in record and not isinstance(record[field], str):
                raise ArtifactError(f"{field} must be a string for {name}")
        if "size" in record and (isinstance(record["size"], bool)
                                 or not isinstance(record["size"], int)
                                 or record["size"] < 0):
            raise ArtifactError(f"size must be a non-negative integer for {name}")
        if not isinstance(record.get("inspection_available"), bool):
            raise ArtifactError(
                f"inspection_available must be boolean for {name}")
        fetch_state = record.get("portage_fetch_state")
        if fetch_state not in PORTAGE_FETCH_STATES:
            raise ArtifactError(
                f"portage_fetch_state is invalid or missing for {name}")
        fetch_evidence = record.get("portage_fetch_evidence")
        if fetch_evidence is not None and not isinstance(fetch_evidence, str):
            raise ArtifactError(
                f"portage_fetch_evidence must be a string for {name}")
        if fetch_state in {"verified", "failed", "superseded-by-ci"} \
                and not fetch_evidence:
            raise ArtifactError(
                f"portage_fetch_evidence is required for {fetch_state} state: {name}")
        result[name] = dict(record)
    return result


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _hash_stream(handle) -> dict[str, str]:
    digests = {
        label: hashlib.new(algorithm)
        for label, algorithm in HASH_NAMES.items()
    }
    while chunk := handle.read(1024 * 1024):
        for digest in digests.values():
            digest.update(chunk)
    return {label: digest.hexdigest() for label, digest in digests.items()}


def _inspect_distfile(path: Path) -> dict[str, Any]:
    try:
        path_before = path.lstat()
    except FileNotFoundError as exc:
        raise _DistfileError(
            "missing-distfile",
            "DIST file is absent from the selected distdir") from exc
    except OSError as exc:
        raise _DistfileError(
            "unreadable-distfile", f"cannot inspect DIST file: {exc}") from exc
    if stat.S_ISLNK(path_before.st_mode):
        raise _DistfileError(
            "nonregular-distfile", "DIST file must not be a symbolic link")
    if not stat.S_ISREG(path_before.st_mode):
        raise _DistfileError(
            "nonregular-distfile", "DIST file must be a regular file")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise _DistfileError(
            "unstable-distfile", "DIST file changed before hashing") from exc
    except OSError as exc:
        raise _DistfileError(
            "unreadable-distfile", f"cannot open DIST file safely: {exc}") from exc

    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened_before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened_before.st_mode)
                    or _file_identity(opened_before) != _file_identity(path_before)):
                raise _DistfileError(
                    "unstable-distfile", "DIST file identity changed before hashing")
            hashes = _hash_stream(handle)
            # A same-size in-place rewrite can share one timestamp tick, so the
            # stat comparison below cannot see it. Re-hash and require a match.
            handle.seek(0)
            confirmation = _hash_stream(handle)
            opened_after = os.fstat(handle.fileno())
            if confirmation != hashes:
                raise _DistfileError(
                    "unstable-distfile", "DIST file changed during hashing")
    except _DistfileError:
        raise
    except OSError as exc:
        raise _DistfileError(
            "unreadable-distfile", f"cannot hash DIST file: {exc}") from exc

    try:
        path_after = path.lstat()
    except OSError as exc:
        raise _DistfileError(
            "unstable-distfile", "DIST file changed during hashing") from exc
    identity = _file_identity(opened_before)
    if (identity != _file_identity(opened_after)
            or identity != _file_identity(path_after)
            or not stat.S_ISREG(path_after.st_mode)):
        raise _DistfileError(
            "unstable-distfile", "DIST file changed during hashing")
    return {
        "hashes": hashes,
        "path": str(path),
        "size": opened_after.st_size,
    }


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
        record: dict[str, Any] = {
            **entry,
            "evidence": source,
            "local": None,
            "states": {
                "artifact_identity": "manifest-only",
                "inspection_available": (
                    source.get("inspection_available") if source else None),
                "portage_fetch": (
                    source.get("portage_fetch_state") if source else None),
                "portage_fetch_evidence": (
                    source.get("portage_fetch_evidence") if source else None),
            },
        }
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
        if source is not None and not source["inspection_available"]:
            findings.append({
                "code": "inspection-unavailable",
                "message": "artifact bytes were not available for content inspection",
                "name": name,
                "severity": "error",
            })
        if source is not None and source["portage_fetch_state"] \
                not in {"verified", "superseded-by-ci"}:
            findings.append({
                "code": "portage-fetch-unverified",
                "message": (
                    "default Portage fetch usability has not been established"),
                "name": name,
                "state": source["portage_fetch_state"],
                "severity": "error",
            })

        if distdir is not None:
            local = distdir / name
            try:
                local_record = _inspect_distfile(local)
            except _DistfileError as exc:
                findings.append({
                    "code": exc.code,
                    "message": str(exc),
                    "name": name,
                    "severity": "error",
                })
            else:
                record["local"] = local_record
                local_matches = True
                if local_record["size"] != entry["size"]:
                    local_matches = False
                    findings.append({
                        "code": "distfile-size-mismatch",
                        "message": "local DIST file size differs from the Manifest",
                        "name": name,
                        "severity": "error",
                    })
                for label, observed in local_record["hashes"].items():
                    if observed != entry["hashes"][label]:
                        local_matches = False
                        findings.append({
                            "code": "distfile-digest-mismatch",
                            "algorithm": label,
                            "message": "local DIST file digest differs from the Manifest",
                            "name": name,
                            "severity": "error",
                        })
                record["states"]["artifact_identity"] = (
                    "manifest-digest-matched" if local_matches
                    else "local-mismatch")
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
            "Manual artifact availability does not establish default Portage fetch usability.",
            "License and redistribution decisions require separate reviewed evidence.",
        ],
        "manifest": str(manifest),
        "ok": complete and not any(item["severity"] == "error" for item in findings),
        "provenance_established": False,
        "truncated": False,
    }
