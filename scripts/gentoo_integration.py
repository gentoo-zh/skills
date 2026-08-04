#!/usr/bin/env python3
"""Run bounded ebuild fixtures in an isolated Gentoo environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import selectors
import signal
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "integration" / "gentoo" / "fixtures"
IMAGE_LOCK = ROOT / "integration" / "gentoo" / "image-lock.json"
GZH_SOURCE = ROOT / "gzh"
REPOSITORY_NAME = "gentoo-zh"
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
COMMAND_TIMEOUT = 300
MAX_METADATA_BYTES = 256 * 1024
MAX_ELOG_FILES = 64
MAX_ELOG_TOTAL_BYTES = 256 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}")
REVISION_RE = re.compile(r"[0-9a-f]{40}")
ATOM_RE = re.compile(
    r"(?P<category>[a-z0-9][a-z0-9+_.-]*)/"
    r"(?P<pf>[a-z0-9][a-z0-9+_-]*-[0-9][a-zA-Z0-9+_.-]*)")

if str(GZH_SOURCE) not in sys.path:
    sys.path.insert(0, str(GZH_SOURCE))

from gzh.verify_install import run_verify_install


class IntegrationError(RuntimeError):
    """Reject incomplete or inconsistent integration evidence."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_regular_file(path: Path, maximum: int = MAX_METADATA_BYTES) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise IntegrationError(f"metadata file is invalid or oversized: {path}")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise IntegrationError(f"metadata file exceeds {maximum} bytes: {path}")
        content = b"".join(chunks)
        after = os.fstat(descriptor)
        if (len(content) != before.st_size
                or (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns)):
            raise IntegrationError(f"metadata file identity changed: {path}")
        return content
    finally:
        os.close(descriptor)


def file_evidence(path: Path, content: bytes) -> dict:
    return {
        "path": str(path),
        "bytes": len(content),
        "sha256": sha256_bytes(content),
    }


def tree_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def relative_file(root: Path, raw: object, field: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise IntegrationError(f"{field} must be a non-empty string")
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise IntegrationError(f"{field} must remain below the fixture root")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise IntegrationError(f"{field} does not identify a fixture file: {raw}")
    return resolved


def load_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IntegrationError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrationError(f"{path} must contain one JSON object")
    return value


def validate_image_lock(data: dict) -> dict:
    required = {
        "schema", "image", "tag", "digest", "reference", "platform",
        "resolved_at", "official_project", "project_revision", "registry",
    }
    if set(data) != required or data.get("schema") != 1:
        raise IntegrationError("image lock schema is invalid")
    digest = data.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise IntegrationError("image lock digest is invalid")
    expected_reference = f"{data['image']}:{data['tag']}@{digest}"
    if data.get("reference") != expected_reference:
        raise IntegrationError("image lock reference does not match its fields")
    if data.get("platform") != "linux/amd64":
        raise IntegrationError("fixture foundation requires linux/amd64")
    revision = data.get("project_revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise IntegrationError("image project revision is invalid")
    for field in ("official_project", "registry"):
        if not str(data.get(field, "")).startswith("https://"):
            raise IntegrationError(f"image lock {field} must use HTTPS")
    return data


def validate_manifest(data: dict, fixture_root: Path = FIXTURES) -> list[dict]:
    if set(data) != {"schema", "scope", "cases"} or data.get("schema") != 1:
        raise IntegrationError("fixture manifest schema is invalid")
    if not isinstance(data.get("scope"), str) or "not an overlay" not in data["scope"]:
        raise IntegrationError("fixture scope must reject full-matrix claims")
    cases = data.get("cases")
    if not isinstance(cases, list) or len(cases) != 2:
        raise IntegrationError("fixture manifest must contain two bounded cases")
    required = {
        "id", "atom", "ebuild", "installed_path", "artifact_sha256",
        "expected_gate", "expected_elog_pattern",
    }
    identifiers: set[str] = set()
    gates: set[str] = set()
    validated = []
    for case in cases:
        if not isinstance(case, dict) or set(case) != required:
            raise IntegrationError("fixture case schema is invalid")
        identifier = case.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9-]+", identifier):
            raise IntegrationError("fixture case id is invalid")
        if identifier in identifiers:
            raise IntegrationError(f"duplicate fixture case id: {identifier}")
        identifiers.add(identifier)
        atom = case.get("atom")
        match = ATOM_RE.fullmatch(atom) if isinstance(atom, str) else None
        if match is None:
            raise IntegrationError(f"invalid fixture atom: {atom}")
        ebuild = relative_file(fixture_root, case.get("ebuild"), "ebuild")
        if ebuild.name != f"{match.group('pf')}.ebuild":
            raise IntegrationError(f"fixture atom and ebuild disagree: {identifier}")
        installed = Path(str(case.get("installed_path", "")))
        if (not installed.parts or installed.is_absolute()
                or ".." in installed.parts or installed.as_posix().startswith("./")):
            raise IntegrationError(f"invalid installed_path: {identifier}")
        checksum = case.get("artifact_sha256")
        if not isinstance(checksum, str) or SHA256_RE.fullmatch(checksum) is None:
            raise IntegrationError(f"invalid artifact checksum: {identifier}")
        gate = case.get("expected_gate")
        if gate not in {"accept", "reject"}:
            raise IntegrationError(f"invalid expected gate: {identifier}")
        gates.add(gate)
        pattern = case.get("expected_elog_pattern")
        if gate == "accept" and pattern is not None:
            raise IntegrationError("accepted fixture cannot expect elog")
        if gate == "reject" and (not isinstance(pattern, str) or not pattern):
            raise IntegrationError("rejected fixture requires an elog pattern")
        validated.append({**case, "ebuild_path": ebuild})
    if gates != {"accept", "reject"}:
        raise IntegrationError("fixtures must cover accept and reject gates")
    return validated


def stop_process_group(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_bounded(
        command: Sequence[str], *, environment: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: int = COMMAND_TIMEOUT,
        maximum: int = MAX_OUTPUT_BYTES) -> dict:
    args = [str(value) for value in command]
    started = time.monotonic()
    report = {
        "command": args,
        "cwd": str(cwd.resolve()) if cwd else None,
        "returncode": None,
        "duration_seconds": None,
        "stdout": "",
        "stderr": "",
        "stdout_bytes": 0,
        "stderr_bytes": 0,
        "complete": False,
        "timed_out": False,
        "truncated": False,
        "error": None,
    }
    process = None
    selector = selectors.DefaultSelector()
    streams: dict[int, bytearray] = {}
    try:
        process = subprocess.Popen(
            args, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=str(cwd) if cwd else None, start_new_session=True)
        if process.stdout is None or process.stderr is None:
            raise IntegrationError("cannot capture command output")
        streams = {
            process.stdout.fileno(): bytearray(),
            process.stderr.fileno(): bytearray(),
        }
        selector.register(process.stdout, selectors.EVENT_READ)
        selector.register(process.stderr, selectors.EVENT_READ)
        deadline = started + timeout
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report["timed_out"] = True
                raise IntegrationError(f"command timed out after {timeout} seconds")
            for key, _events in selector.select(min(remaining, 0.25)):
                size = sum(len(value) for value in streams.values())
                chunk = os.read(key.fd, min(65536, maximum - size + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[key.fd].extend(chunk)
                if sum(len(value) for value in streams.values()) > maximum:
                    report["truncated"] = True
                    raise IntegrationError(
                        f"command output exceeds {maximum} bytes")
        report["returncode"] = process.wait(
            timeout=max(0.1, deadline - time.monotonic()))
        report["complete"] = True
    except Exception as exc:
        if process is not None:
            stop_process_group(process)
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        selector.close()
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        if streams:
            values = list(streams.values())
            report["stdout"] = bytes(values[0][:maximum]).decode(errors="replace")
            report["stderr"] = bytes(values[1][:maximum]).decode(errors="replace")
            report["stdout_bytes"] = len(values[0])
            report["stderr_bytes"] = len(values[1])
        if process is not None:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
    return report


def write_command_evidence(directory: Path, name: str, evidence: dict) -> dict:
    stdout_path = directory / f"{name}.stdout.log"
    stderr_path = directory / f"{name}.stderr.log"
    stdout_path.write_text(evidence.pop("stdout"), encoding="utf-8")
    stderr_path.write_text(evidence.pop("stderr"), encoding="utf-8")
    evidence["stdout_log"] = str(stdout_path)
    evidence["stderr_log"] = str(stderr_path)
    evidence["stdout_sha256"] = sha256_file(stdout_path)
    evidence["stderr_sha256"] = sha256_file(stderr_path)
    return evidence


def write_portage_config(config_root: Path, overlay: Path) -> str:
    portage_config = config_root / "etc" / "portage"
    repos = portage_config / "repos.conf"
    repos.mkdir(parents=True)
    profile = overlay / "profiles" / "integration" / "amd64"
    repository_configuration = (
        "[DEFAULT]\n"
        f"main-repo = {REPOSITORY_NAME}\n\n"
        f"[{REPOSITORY_NAME}]\n"
        f"location = {overlay}\n"
        "auto-sync = no\n")
    (repos / f"{REPOSITORY_NAME}.conf").write_text(
        repository_configuration,
        encoding="utf-8")
    os.symlink(profile, portage_config / "make.profile")
    (portage_config / "make.conf").write_text(
        'ACCEPT_LICENSE="*"\n'
        'MAKEOPTS="-j1"\n'
        'NOCOLOR="true"\n',
        encoding="utf-8")
    return repository_configuration


def configure_portage(runtime: Path, overlay: Path) -> tuple[Path, dict[str, str]]:
    config_root = runtime / "config-root"
    repository_configuration = write_portage_config(config_root, overlay)
    for directory in (
            runtime / "portage-tmp", runtime / "distfiles",
            runtime / "packages", runtime / "emerge-logs"):
        directory.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update({
        "PORTAGE_CONFIGROOT": str(config_root) + os.sep,
        "PORTAGE_TMPDIR": str(runtime / "portage-tmp"),
        "DISTDIR": str(runtime / "distfiles"),
        "PKGDIR": str(runtime / "packages"),
        "EMERGE_LOG_DIR": str(runtime / "emerge-logs"),
        "PORTAGE_REPOSITORIES": repository_configuration,
        "LC_ALL": "C",
    })
    return config_root, environment


def elog_inventory(log_root: Path) -> list[dict]:
    elog_root = log_root / "elog"
    try:
        path_info = elog_root.lstat()
    except FileNotFoundError:
        return []
    if not stat.S_ISDIR(path_info.st_mode):
        raise IntegrationError(f"elog root is not a directory: {elog_root}")
    if not hasattr(os, "O_NOFOLLOW"):
        raise IntegrationError("elog inventory requires O_NOFOLLOW support")

    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(elog_root, directory_flags)
    except OSError as exc:
        raise IntegrationError(f"cannot open elog root safely: {elog_root}: {exc}") from exc
    try:
        directory_info = os.fstat(directory_fd)
        if ((path_info.st_dev, path_info.st_ino)
                != (directory_info.st_dev, directory_info.st_ino)):
            raise IntegrationError(
                f"elog root identity changed while opening: {elog_root}")
        names = sorted(os.listdir(directory_fd))
        if len(names) > MAX_ELOG_FILES:
            raise IntegrationError(
                f"elog inventory exceeds {MAX_ELOG_FILES} entries")

        records = []
        snapshots: dict[str, os.stat_result] = {}
        total = 0
        for name in names:
            path = elog_root / name
            try:
                before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise IntegrationError(
                    f"cannot inspect elog entry safely: {path}: {exc}") from exc
            if not stat.S_ISREG(before.st_mode):
                raise IntegrationError(f"elog entry is not a regular file: {path}")
            if total + before.st_size > MAX_ELOG_TOTAL_BYTES:
                raise IntegrationError(
                    f"elog inventory exceeds {MAX_ELOG_TOTAL_BYTES} bytes")

            file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                file_fd = os.open(name, file_flags, dir_fd=directory_fd)
            except OSError as exc:
                raise IntegrationError(
                    f"cannot open elog entry safely: {path}: {exc}") from exc
            try:
                opened = os.fstat(file_fd)
                if ((before.st_dev, before.st_ino)
                        != (opened.st_dev, opened.st_ino)):
                    raise IntegrationError(
                        f"elog entry identity changed while opening: {path}")
                if not stat.S_ISREG(opened.st_mode):
                    raise IntegrationError(
                        f"elog entry is not a regular file: {path}")

                chunks = []
                read_bytes = 0
                while True:
                    remaining = MAX_ELOG_TOTAL_BYTES - total - read_bytes
                    chunk = os.read(file_fd, min(65536, remaining + 1))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    read_bytes += len(chunk)
                    if total + read_bytes > MAX_ELOG_TOTAL_BYTES:
                        raise IntegrationError(
                            f"elog inventory exceeds {MAX_ELOG_TOTAL_BYTES} bytes")
                content_bytes = b"".join(chunks)
                after = os.fstat(file_fd)
            finally:
                os.close(file_fd)

            identity_before = (before.st_dev, before.st_ino)
            identity_after = (after.st_dev, after.st_ino)
            metadata_before = (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            metadata_after = (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            if (identity_before != identity_after
                    or metadata_before != metadata_after
                    or len(content_bytes) != before.st_size):
                raise IntegrationError(f"elog entry changed while reading: {path}")
            total += len(content_bytes)
            snapshots[name] = after
            records.append({
                "path": str(path),
                "bytes": len(content_bytes),
                "sha256": sha256_bytes(content_bytes),
                "content": content_bytes.decode(encoding="utf-8", errors="replace"),
            })

        if sorted(os.listdir(directory_fd)) != names:
            raise IntegrationError(
                f"elog directory changed while reading: {elog_root}")
        for name, expected in snapshots.items():
            try:
                observed = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise IntegrationError(
                    f"elog entry changed after reading: {elog_root / name}: {exc}") from exc
            expected_state = (
                expected.st_dev, expected.st_ino, expected.st_size,
                expected.st_mtime_ns, expected.st_ctime_ns)
            observed_state = (
                observed.st_dev, observed.st_ino, observed.st_size,
                observed.st_mtime_ns, observed.st_ctime_ns)
            if expected_state != observed_state:
                raise IntegrationError(
                    f"elog entry changed after reading: {elog_root / name}")
        final_directory_info = os.fstat(directory_fd)
        directory_state = (
            directory_info.st_dev, directory_info.st_ino,
            directory_info.st_mtime_ns, directory_info.st_ctime_ns)
        final_directory_state = (
            final_directory_info.st_dev, final_directory_info.st_ino,
            final_directory_info.st_mtime_ns, final_directory_info.st_ctime_ns)
        if directory_state != final_directory_state:
            raise IntegrationError(
                f"elog directory changed while reading: {elog_root}")
        try:
            final_path_info = elog_root.lstat()
        except OSError as exc:
            raise IntegrationError(
                f"elog root changed while reading: {elog_root}: {exc}") from exc
        if ((final_path_info.st_dev, final_path_info.st_ino)
                != (directory_info.st_dev, directory_info.st_ino)):
            raise IntegrationError(
                f"elog root identity changed while reading: {elog_root}")
        return records
    finally:
        os.close(directory_fd)


def source_merge_command(case: dict) -> list[str]:
    return [
        "emerge", "--oneshot", "--selective=n", "--nodeps",
        "--usepkg=n", "--verbose",
        f"={case['atom']}::{REPOSITORY_NAME}",
    ]


def prepare_portage_runtime(path: Path) -> None:
    before = path.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise IntegrationError(f"Portage runtime is not a directory: {path}")
    path.chmod(0o711)
    after = path.lstat()
    if ((before.st_dev, before.st_ino) != (after.st_dev, after.st_ino)
            or stat.S_IMODE(after.st_mode) != 0o711):
        raise IntegrationError(f"cannot make Portage runtime traversable: {path}")


def evaluate_verifier(case: dict, verifier: dict) -> dict:
    elog_text = "\n".join(
        record.get("text", "") for record in verifier.get("elog_files", []))
    if case["expected_gate"] == "accept":
        matched = (
            verifier.get("complete") is True
            and verifier.get("ok") is True
            and verifier.get("failed_step") is None)
    else:
        matched = (
            verifier.get("complete") is True
            and verifier.get("ok") is False
            and verifier.get("failed_step") == "elog"
            and case["expected_elog_pattern"] in elog_text)
    return {
        "complete": verifier.get("complete") is True,
        "actual_gate": "accept" if verifier.get("ok") is True else "reject",
        "expected_gate": case["expected_gate"],
        "failed_step": verifier.get("failed_step"),
        "matched": matched,
    }


def evaluate_case(
        case: dict, commands: list[dict], artifact: dict,
        elog: list[dict], cleanup: dict) -> dict:
    phases_ok = bool(commands) and all(
        item["complete"] and item["returncode"] == 0
        and not item["timed_out"] and not item["truncated"]
        for item in commands)
    artifact_ok = (
        artifact.get("exists") is True
        and artifact.get("regular") is True
        and artifact.get("executable") is True
        and artifact.get("vdb_contents_exists") is True
        and artifact.get("sha256") == case["artifact_sha256"])
    actual_gate = "accept" if phases_ok and artifact_ok and not elog else "reject"
    elog_text = "\n".join(record["content"] for record in elog)
    expected_pattern = case["expected_elog_pattern"]
    boundary_verified = (
        case["expected_gate"] == "reject"
        and phases_ok and artifact_ok and bool(elog)
        and expected_pattern in elog_text)
    cleanup_ok = (
        cleanup["complete"] and cleanup["returncode"] == 0
        and not cleanup["timed_out"] and not cleanup["truncated"])
    matched = (
        actual_gate == case["expected_gate"]
        and cleanup_ok
        and (case["expected_gate"] == "accept" or boundary_verified))
    return {
        "phase_commands_ok": phases_ok,
        "artifact_ok": artifact_ok,
        "actual_gate": actual_gate,
        "expected_gate": case["expected_gate"],
        "boundary_verified": boundary_verified,
        "cleanup_ok": cleanup_ok,
        "matched": matched,
    }


def run_case(
        case: dict, base_environment: dict[str, str], output: Path,
        overlay: Path,
) -> dict:
    case_output = output / "cases" / case["id"]
    command_output = case_output / "commands"
    log_root = case_output / "portage-logs"
    command_output.mkdir(parents=True)
    log_root.mkdir(parents=True)
    environment = base_environment.copy()
    environment.update({
        "PORTAGE_LOGDIR": str(log_root),
        "PORTAGE_ELOG_CLASSES": "qa warn error",
        "PORTAGE_ELOG_SYSTEM": "save",
        "FEATURES": f"{base_environment.get('FEATURES', '')} test".strip(),
    })
    atom = ATOM_RE.fullmatch(case["atom"])
    assert atom is not None
    target_root = (
        Path(environment["PORTAGE_TMPDIR"]).parent / "roots" / case["id"])
    target_root.mkdir(parents=True)
    write_portage_config(target_root, overlay)
    environment["ROOT"] = str(target_root) + os.sep
    environment["SYSROOT"] = str(target_root) + os.sep
    environment["PORTAGE_CONFIGROOT"] = str(target_root) + os.sep
    execution_ebuild = overlay / case["ebuild_path"].relative_to(
        FIXTURES / "overlay")
    execution_case = {**case, "ebuild_path": execution_ebuild}
    commands = [write_command_evidence(
        command_output, "01-emerge",
        run_bounded(source_merge_command(execution_case), environment=environment))]

    image_path = target_root / case["installed_path"]
    vdb_contents = (
        target_root / "var" / "db" / "pkg" / atom.group("category")
        / atom.group("pf") / "CONTENTS")
    artifact = {
        "path": str(image_path),
        "exists": image_path.exists(),
        "regular": image_path.is_file(),
        "executable": image_path.is_file() and os.access(image_path, os.X_OK),
        "bytes": image_path.stat().st_size if image_path.is_file() else None,
        "sha256": sha256_file(image_path) if image_path.is_file() else None,
        "vdb_contents": str(vdb_contents),
        "vdb_contents_exists": vdb_contents.is_file(),
        "vdb_contents_sha256": (
            sha256_file(vdb_contents) if vdb_contents.is_file() else None),
    }
    elog = elog_inventory(log_root)
    verifier = run_verify_install(
        execution_ebuild,
        logdir=case_output / "verifier-portage-logs",
        timeout=COMMAND_TIMEOUT,
        max_output_bytes=MAX_OUTPUT_BYTES,
        environment=environment,
        runner=None,
    )
    verifier_decision = evaluate_verifier(case, verifier)
    cleanup = write_command_evidence(
        command_output, "99-cleanup",
        run_bounded(
            ["ebuild", str(execution_ebuild), "clean"],
            environment=environment))
    decision = evaluate_case(case, commands, artifact, elog, cleanup)
    decision["verifier_matched"] = verifier_decision["matched"]
    decision["matched"] = decision["matched"] and verifier_decision["matched"]
    return {
        "id": case["id"],
        "atom": case["atom"],
        "ebuild": str(execution_ebuild),
        "ebuild_sha256": sha256_file(execution_ebuild),
        "environment": {
            key: environment[key]
            for key in (
                "ROOT", "SYSROOT", "PORTAGE_CONFIGROOT", "PORTAGE_TMPDIR",
                "PORTAGE_LOGDIR", "PORTAGE_ELOG_CLASSES",
                "PORTAGE_ELOG_SYSTEM", "FEATURES")
        },
        "commands": commands,
        "artifact": artifact,
        "elog": elog,
        "verifier": verifier,
        "verifier_decision": verifier_decision,
        "cleanup": cleanup,
        "decision": decision,
    }


def tool_versions() -> dict:
    tools = {}
    for name, command in {
            "emerge": ["emerge", "--version"],
            "ebuild": ["ebuild", "--help"],
            "bash": ["bash", "--version"],
            "sandbox": ["sandbox", "--version"],
            "git": ["git", "--version"],
    }.items():
        tools[name] = run_bounded(command, timeout=20, maximum=64 * 1024)
    try:
        import portage
        portage_version = str(portage.VERSION)
    except Exception as exc:
        portage_version = f"unavailable: {type(exc).__name__}: {exc}"
    complete = (
        not portage_version.startswith("unavailable:")
        and all(evidence["complete"] and evidence["returncode"] == 0
                for evidence in tools.values()))
    return {
        "complete": complete,
        "python": sys.version,
        "portage_module": portage_version,
        "commands": tools,
    }


def collect_repository_evidence(repository_root: Path) -> dict:
    name_path = repository_root / "profiles" / "repo_name"
    manifest_path = repository_root / "Manifest"
    timestamp_path = repository_root / "metadata" / "timestamp.commit"
    name = read_regular_file(name_path, 64)
    manifest = read_regular_file(manifest_path)
    timestamp = read_regular_file(timestamp_path, 256)
    try:
        repository_name = name.decode("utf-8").strip()
        manifest_text = manifest.decode("utf-8")
        timestamp_text = timestamp.decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise IntegrationError("Gentoo repository evidence is not UTF-8") from exc
    if repository_name != "gentoo":
        raise IntegrationError("Gentoo repository snapshot has the wrong name")
    if (not manifest_text.startswith("-----BEGIN PGP SIGNED MESSAGE-----\n")
            or "\n-----BEGIN PGP SIGNATURE-----\n" not in manifest_text
            or "\n-----END PGP SIGNATURE-----\n" not in manifest_text):
        raise IntegrationError("Gentoo repository Manifest is not signed")
    manifest_timestamps = re.findall(
        r"^TIMESTAMP ([^\r\n]+)$", manifest_text, flags=re.MULTILINE)
    if len(manifest_timestamps) != 1:
        raise IntegrationError("Gentoo repository Manifest timestamp is invalid")
    timestamp_match = re.fullmatch(
        r"([0-9a-f]{40}) [0-9]+ ([^\s]+)", timestamp_text)
    if timestamp_match is None:
        raise IntegrationError("Gentoo repository revision evidence is invalid")
    return {
        "root": str(repository_root),
        "name": {**file_evidence(name_path, name), "value": repository_name},
        "manifest": {
            **file_evidence(manifest_path, manifest),
            "signed_timestamp": manifest_timestamps[0],
        },
        "timestamp_commit": {
            **file_evidence(timestamp_path, timestamp),
            "revision": timestamp_match.group(1),
            "timestamp": timestamp_match.group(2),
        },
    }


def collect_git_evidence(vdb_root: Path, repository_revision: str) -> dict:
    category = vdb_root / "dev-vcs"
    try:
        candidates = sorted(
            path for path in category.iterdir() if path.name.startswith("git-"))
    except FileNotFoundError as exc:
        raise IntegrationError("installed Git VDB category is missing") from exc
    if len(candidates) != 1:
        raise IntegrationError(
            f"expected one installed Git VDB entry, found {len(candidates)}")
    entry = candidates[0]
    entry_info = entry.lstat()
    if not stat.S_ISDIR(entry_info.st_mode):
        raise IntegrationError("installed Git VDB entry is not a directory")

    fields = {}
    for name, maximum in (
            ("PF", 256), ("USE", 16 * 1024), ("REPO_REVISIONS", 16 * 1024),
            ("repository", 256)):
        path = entry / name
        content = read_regular_file(path, maximum)
        try:
            value = content.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise IntegrationError(f"installed Git {name} is not UTF-8") from exc
        fields[name] = {**file_evidence(path, content), "value": value}

    package = fields["PF"]["value"]
    if package != entry.name or not re.fullmatch(r"git-[0-9][A-Za-z0-9+_.-]*", package):
        raise IntegrationError("installed Git PF does not match its VDB entry")
    if fields["repository"]["value"] != "gentoo":
        raise IntegrationError("installed Git did not come from the Gentoo repository")
    try:
        revisions = json.loads(fields["REPO_REVISIONS"]["value"])
    except json.JSONDecodeError as exc:
        raise IntegrationError("installed Git REPO_REVISIONS is invalid") from exc
    if (not isinstance(revisions, dict)
            or revisions.get("gentoo") != repository_revision
            or not REVISION_RE.fullmatch(str(revisions.get("gentoo", "")))):
        raise IntegrationError(
            "installed Git revision does not match the synced repository")
    use_flags = fields["USE"]["value"].split()
    if "safe-directory" not in use_flags:
        raise IntegrationError("installed Git is missing the requested USE flag")
    ebuild_path = entry / f"{package}.ebuild"
    ebuild = read_regular_file(ebuild_path)
    return {
        "vdb_path": str(entry),
        "atom": f"dev-vcs/{package}",
        "pf": fields["PF"],
        "use": {
            **fields["USE"],
            "flags": use_flags,
        },
        "repository": fields["repository"],
        "repo_revisions": {
            **fields["REPO_REVISIONS"],
            "repositories": revisions,
        },
        "ebuild": file_evidence(ebuild_path, ebuild),
    }


def require_clean_bootstrap_root(
        repository_root: Path, vdb_root: Path, environment: dict[str, str]) -> dict:
    try:
        repository_root.lstat()
    except FileNotFoundError:
        repository_present = False
    else:
        repository_present = True
    category = vdb_root / "dev-vcs"
    try:
        git_entries = sorted(
            path.name for path in category.iterdir() if path.name.startswith("git-"))
    except FileNotFoundError:
        git_entries = []
    git_path = shutil.which("git", path=environment.get("PATH"))
    evidence = {
        "repository_present": repository_present,
        "git_vdb_entries": git_entries,
        "git_on_path": git_path,
    }
    if repository_present or git_entries or git_path is not None:
        raise IntegrationError("Gentoo bootstrap root is not clean")
    return evidence


def bootstrap(output: Path) -> dict:
    if os.geteuid() != 0:
        raise IntegrationError("Gentoo bootstrap requires root in isolation")
    try:
        output.lstat()
    except FileNotFoundError:
        pass
    else:
        raise IntegrationError(f"output path already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    observed_image = os.environ.get("GZH_GENTOO_IMAGE")
    repository_root = Path("/var/db/repos/gentoo")
    vdb_root = Path("/var/db/pkg")
    report = {
        "schema": 1,
        "ok": False,
        "complete": False,
        "image_lock": None,
        "observed_image_reference": observed_image,
        "environment": {"USE": "-* safe-directory"},
        "preconditions": None,
        "repository": None,
        "git": None,
        "commands": [],
        "errors": [],
    }
    try:
        command_output = output / "commands"
        command_output.mkdir()
        lock = validate_image_lock(load_json_object(IMAGE_LOCK))
        report["image_lock"] = lock
        if observed_image != lock["reference"]:
            raise IntegrationError(
                "GZH_GENTOO_IMAGE does not match the reviewed image lock")
        base_environment = os.environ.copy()
        report["preconditions"] = require_clean_bootstrap_root(
            repository_root, vdb_root, base_environment)
        for name, command, overrides in (
                ("01-emerge-webrsync", ["emerge-webrsync"], {}),
                ("02-emerge-git", [
                    "emerge", "--oneshot", "--usepkg=n", "dev-vcs/git",
                ], {"USE": "-* safe-directory"}),
                ("03-git-version", ["git", "--version"], {})):
            environment = base_environment.copy()
            environment.update(overrides)
            evidence = write_command_evidence(
                command_output, name,
                run_bounded(command, environment=environment))
            report["commands"].append({"name": name, **evidence})
            if (not evidence["complete"] or evidence["returncode"] != 0
                    or evidence["timed_out"] or evidence["truncated"]):
                raise IntegrationError(f"bootstrap command failed: {name}")
            if name == "01-emerge-webrsync":
                report["repository"] = collect_repository_evidence(repository_root)
            elif name == "02-emerge-git":
                report["git"] = collect_git_evidence(
                    vdb_root,
                    report["repository"]["timestamp_commit"]["revision"])
        report["complete"] = (
            len(report["commands"]) == 3
            and report["preconditions"] is not None
            and report["repository"] is not None
            and report["git"] is not None
            and all(command["complete"] for command in report["commands"]))
        report["ok"] = report["complete"]
    except Exception as exc:
        report["errors"].append({
            "type": type(exc).__name__, "message": str(exc)})
    (output / "bootstrap.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def execute(output: Path) -> dict:
    if os.geteuid() != 0:
        raise IntegrationError("real Gentoo fixture execution requires root in isolation")
    if output.exists() and any(output.iterdir()):
        raise IntegrationError(f"output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    lock = validate_image_lock(load_json_object(IMAGE_LOCK))
    observed_image = os.environ.get("GZH_GENTOO_IMAGE")
    if observed_image != lock["reference"]:
        raise IntegrationError("GZH_GENTOO_IMAGE does not match the reviewed image lock")
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        raise IntegrationError("fixture execution requires a linux/amd64 environment")
    manifest = load_json_object(FIXTURES / "manifest.json")
    cases = validate_manifest(manifest)
    report = {
        "schema": 1,
        "ok": False,
        "complete": False,
        "scope": manifest["scope"],
        "image_lock": lock,
        "observed_image_reference": observed_image,
        "fixture_tree_sha256": tree_sha256(FIXTURES),
        "repository_revision": os.environ.get("GITHUB_SHA"),
        "runtime": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "euid": os.geteuid(),
        },
        "tools": tool_versions(),
        "fixture_repository": None,
        "cases": [],
        "errors": [],
    }
    (output / "tool-versions.json").write_text(
        json.dumps(report["tools"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    try:
        with tempfile.TemporaryDirectory(
                prefix="gzh-gentoo-integration-", dir="/var/tmp") as temporary:
            runtime = Path(temporary)
            prepare_portage_runtime(runtime)
            overlay = runtime / "overlay"
            shutil.copytree(FIXTURES / "overlay", overlay)
            repository = run_bounded(["git", "init", "-q"], cwd=overlay)
            report["fixture_repository"] = repository
            if (not repository["complete"] or repository["returncode"] != 0
                    or repository["timed_out"] or repository["truncated"]):
                raise IntegrationError(
                    "cannot create the isolated fixture Git checkout")
            _config_root, environment = configure_portage(
                runtime, overlay)
            for case in cases:
                report["cases"].append(
                    run_case(case, environment, output, overlay))
        report["complete"] = (
            report["tools"]["complete"]
            and report["fixture_repository"]["complete"]
            and len(report["cases"]) == len(cases)
            and all(
                case["decision"]["cleanup_ok"]
                and case["verifier_decision"]["complete"]
                and all(command["complete"] for command in case["commands"])
                for case in report["cases"]))
        report["ok"] = (
            report["complete"]
            and all(case["decision"]["matched"] for case in report["cases"]))
    except Exception as exc:
        report["errors"].append({
            "type": type(exc).__name__, "message": str(exc)})
    (output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--validate-only", action="store_true",
        help="validate the lock and fixture contract without executing Portage")
    mode.add_argument(
        "--bootstrap", action="store_true",
        help="bootstrap official Gentoo inputs with bounded failure evidence")
    mode.add_argument(
        "--execute", action="store_true",
        help="execute the fixtures in an isolated root Gentoo environment")
    parser.add_argument(
        "--output", type=Path,
        help="evidence directory required by --bootstrap or --execute")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.validate_only:
            lock = validate_image_lock(load_json_object(IMAGE_LOCK))
            cases = validate_manifest(load_json_object(FIXTURES / "manifest.json"))
            print(json.dumps({
                "schema": 1,
                "ok": True,
                "image_reference": lock["reference"],
                "fixture_tree_sha256": tree_sha256(FIXTURES),
                "case_ids": [case["id"] for case in cases],
            }, indent=2, sort_keys=True))
            return 0
        if args.bootstrap:
            if args.output is None:
                raise IntegrationError("--bootstrap requires --output")
            report = bootstrap(args.output.resolve())
            return 0 if report["ok"] else 1
        if args.output is None:
            raise IntegrationError("--execute requires --output")
        report = execute(args.output.resolve())
        return 0 if report["ok"] else 1
    except (IntegrationError, OSError, ValueError) as exc:
        print(f"gentoo integration failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
