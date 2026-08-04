from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


SCHEMA = "gzh.executor-evidence/v1"
MAX_FINAL_LOG_BYTES = 1024 * 1024
MAX_ELOG_BYTES = 256 * 1024
MAX_ELOG_FILES = 64
MAX_ELOG_TOTAL_BYTES = 2 * 1024 * 1024
MAX_INVENTORY_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_COMMANDS = 32
REDACTED = "[REDACTED]"

_SECRET_KEY_RE = re.compile(
    r"(?:auth|cookie|credential|github|identity|pass(?:word)?|secret|token)",
    re.IGNORECASE,
)
_SSH_VALUE_OPTIONS = {"-F", "-i", "-J", "-l", "-o", "-p", "-S"}


class EvidenceError(ValueError):
    pass


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_json(value: Any) -> bytes:
    return (json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ) + "\n").encode()


def _bounded_bytes(value: str | bytes, maximum: int, label: str) -> bytes:
    content = value.encode() if isinstance(value, str) else bytes(value)
    if len(content) > maximum:
        raise EvidenceError(f"{label} exceeds the {maximum}-byte evidence limit")
    return content


def _artifact_record(path: str, content: bytes) -> dict[str, Any]:
    return {"path": path, "size": len(content), "sha256": _sha256_bytes(content)}


def _safe_elog_name(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise EvidenceError("elog names must be non-empty base names")
    if Path(value).name != value or "/" in value or "\\" in value:
        raise EvidenceError(f"elog name is not a base name: {value!r}")
    if any(ord(character) < 32 for character in value):
        raise EvidenceError("elog names must not contain control characters")
    return value


def redact_environment(environment: Mapping[str, object]) -> dict[str, str]:
    """Return the small, printable environment representation allowed in evidence."""
    redacted: dict[str, str] = {}
    for key, raw_value in sorted(environment.items()):
        if not isinstance(key, str):
            raise EvidenceError("environment keys must be strings")
        value = str(raw_value)
        redacted[key] = REDACTED if _SECRET_KEY_RE.search(key) else value
    return redacted


def redact_argv(
    argv: Sequence[object], *, sensitive_values: Iterable[object] = (),
) -> list[str]:
    """Redact credentials and SSH connection details from a command vector."""
    secrets = {str(value) for value in sensitive_values if str(value)}
    result: list[str] = []
    redact_next = False
    ssh_command = bool(argv and Path(str(argv[0])).name in {"ssh", "scp", "sftp"})
    for index, raw_value in enumerate(argv):
        value = str(raw_value)
        if redact_next or value in secrets:
            result.append(REDACTED)
            redact_next = False
            continue
        if ssh_command and value in _SSH_VALUE_OPTIONS:
            result.append(value)
            redact_next = True
            continue
        if ssh_command and index > 0 and not value.startswith("-") and "@" in value:
            result.append(REDACTED)
            continue
        if _SECRET_KEY_RE.search(value.partition("=")[0]) and "=" in value:
            result.append(f"{value.partition('=')[0]}={REDACTED}")
            continue
        result.append(value)
    return result


def command_record(
    argv: Sequence[object], *, environment: Mapping[str, object] | None = None,
    sensitive_values: Iterable[object] = (),
) -> dict[str, Any]:
    return {
        "argv": redact_argv(argv, sensitive_values=sensitive_values),
        "environment": redact_environment(environment or {}),
    }


def _normalize_command(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"argv", "environment"}:
        raise EvidenceError("command records require only argv and environment")
    argv = value["argv"]
    environment = value["environment"]
    if (not isinstance(argv, Sequence) or isinstance(argv, (str, bytes))
            or not isinstance(environment, Mapping)):
        raise EvidenceError("command argv and environment have invalid types")
    return command_record(argv, environment=environment)


def create_evidence(
    directory: Path,
    *,
    executor_type: str,
    executor_name: str,
    package: str,
    commit: str,
    commands: Sequence[Mapping[str, Any]],
    started_at: str,
    ended_at: str,
    exit_state: str,
    use_state: Sequence[str],
    arch: str,
    profile: str,
    final_log: str | bytes,
    elogs: Mapping[str, str | bytes],
    installed_inventory: Sequence[str],
    cleanup: Mapping[str, Any],
    retained_dependencies: Sequence[str],
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write one bounded, self-verifying executor record to a fresh directory."""
    if executor_type not in {"local", "ssh"}:
        raise EvidenceError("executor_type must be local or ssh")
    required_strings = {
        "executor_name": executor_name,
        "package": package,
        "commit": commit,
        "started_at": started_at,
        "ended_at": ended_at,
        "exit_state": exit_state,
        "arch": arch,
        "profile": profile,
    }
    for label, value in required_strings.items():
        if not isinstance(value, str) or not value or "\x00" in value:
            raise EvidenceError(f"{label} must be a non-empty string")
    if len(commands) > MAX_COMMANDS:
        raise EvidenceError(f"command inventory exceeds {MAX_COMMANDS} entries")
    normalized_commands = [_normalize_command(value) for value in commands]

    final_content = _bounded_bytes(final_log, MAX_FINAL_LOG_BYTES, "final log")
    inventory_content = _bounded_bytes(
        "".join(f"{item}\n" for item in sorted(set(installed_inventory))),
        MAX_INVENTORY_BYTES,
        "installed inventory",
    )
    if len(elogs) > MAX_ELOG_FILES:
        raise EvidenceError(f"elog inventory exceeds {MAX_ELOG_FILES} files")

    artifacts: list[dict[str, Any]] = []
    contents: list[tuple[str, bytes]] = [
        ("logs/final.log", final_content),
        ("inventory/installed.txt", inventory_content),
    ]
    elog_total = 0
    for name, raw_content in sorted(elogs.items()):
        name = _safe_elog_name(name)
        content = _bounded_bytes(raw_content, MAX_ELOG_BYTES, f"elog {name}")
        elog_total += len(content)
        if elog_total > MAX_ELOG_TOTAL_BYTES:
            raise EvidenceError(
                f"elog evidence exceeds the {MAX_ELOG_TOTAL_BYTES}-byte aggregate limit"
            )
        contents.append((f"elogs/{name}", content))

    requested_destination = Path(directory).expanduser()
    if requested_destination.exists() or requested_destination.is_symlink():
        raise EvidenceError(
            f"evidence directory already exists: {requested_destination}")
    destination = requested_destination.resolve()
    destination.mkdir(parents=True, mode=0o700)

    try:
        for relative, content in contents:
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            artifacts.append(_artifact_record(relative, content))

        record: dict[str, Any] = {
            "schema": SCHEMA,
            "executor": {"type": executor_type, "name": executor_name},
            "package": package,
            "commit": commit,
            "started_at": started_at,
            "ended_at": ended_at,
            "exit_state": exit_state,
            "use_state": sorted(set(str(value) for value in use_state)),
            "arch": arch,
            "profile": profile,
            "commands": normalized_commands,
            "artifacts": artifacts,
            "final_log": artifacts[0],
            "elog_inventory": [
                value for value in artifacts if value["path"].startswith("elogs/")
            ],
            "installed_inventory": artifacts[1],
            "cleanup": dict(cleanup),
            "provenance": dict(provenance or {}),
            "retained_dependencies": sorted(set(
                str(value) for value in retained_dependencies
            )),
        }
        unsigned = _canonical_json(record)
        if len(unsigned) > MAX_MANIFEST_BYTES:
            raise EvidenceError(
                f"evidence manifest exceeds the {MAX_MANIFEST_BYTES}-byte limit"
            )
        record["digest"] = _sha256_bytes(unsigned)
        (destination / "evidence.json").write_bytes(_canonical_json(record))
        return {**record, "directory": str(destination)}
    except Exception:
        # The directory is owned by this call and was proven absent above.
        for path in sorted(destination.rglob("*"), reverse=True):
            if path.is_file() and not path.is_symlink():
                path.unlink()
            elif path.is_dir() and not path.is_symlink():
                path.rmdir()
        destination.rmdir()
        raise


def _regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except (FileNotFoundError, OSError):
        return False


def verify_evidence(
    directory: Path, *, expected_digest: str | None = None,
) -> dict[str, Any]:
    """Verify the manifest digest and every declared bounded artifact."""
    root = Path(directory).expanduser().resolve()
    errors: list[str] = []
    manifest_path = root / "evidence.json"
    if not _regular_file(manifest_path):
        return {
            "ok": False,
            "state": "incomplete",
            "directory": str(root),
            "digest": None,
            "errors": ["evidence.json is missing or is not a regular file"],
        }
    try:
        record = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "state": "incomplete",
            "directory": str(root),
            "digest": None,
            "errors": [f"cannot read evidence.json: {exc}"],
        }
    if not isinstance(record, dict) or record.get("schema") != SCHEMA:
        errors.append("unsupported or missing evidence schema")
    stored_digest = record.get("digest") if isinstance(record, dict) else None
    if isinstance(record, dict):
        unsigned = dict(record)
        unsigned.pop("digest", None)
        calculated_digest = _sha256_bytes(_canonical_json(unsigned))
    else:
        calculated_digest = None
    if stored_digest != calculated_digest:
        errors.append("evidence manifest digest does not match its content")
    if expected_digest is not None and stored_digest != expected_digest:
        errors.append("evidence digest does not match the expected digest")

    artifacts = record.get("artifacts", []) if isinstance(record, dict) else []
    if not isinstance(artifacts, list):
        errors.append("artifact inventory is not an array")
        artifacts = []
    declared: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict):
            errors.append("artifact inventory contains a non-object entry")
            continue
        relative = item.get("path")
        if not isinstance(relative, str):
            errors.append("artifact path is missing")
            continue
        candidate = Path(relative)
        if (candidate.is_absolute() or ".." in candidate.parts
                or relative in declared):
            errors.append(f"unsafe or duplicate artifact path: {relative!r}")
            continue
        declared.add(relative)
        path = root / candidate
        if not _regular_file(path):
            errors.append(f"artifact is missing or is not a regular file: {relative}")
            continue
        content = path.read_bytes()
        if len(content) != item.get("size") or _sha256_bytes(content) != item.get("sha256"):
            errors.append(f"artifact digest or size changed: {relative}")

    actual = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    for relative in sorted(actual - declared):
        errors.append(f"undeclared evidence artifact: {relative}")
    return {
        "ok": not errors,
        "state": "complete" if not errors else "incomplete",
        "directory": str(root),
        "digest": stored_digest,
        "errors": errors,
    }
