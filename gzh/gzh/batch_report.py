from __future__ import annotations

import fcntl
import hashlib
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


class BatchReportConflict(RuntimeError):
    pass


def report_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_batch_report(
        directory: Path, content: str, *,
        now: datetime | None = None,
        token_hex: Callable[[int], str] = secrets.token_hex) -> Path:
    """Reserve a unique report path and durably write its first checkpoint."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    instant = now or datetime.now(timezone.utc)
    timestamp = instant.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(128):
        path = directory / f"bump-batch-{timestamp}-{token_hex(4)}.md"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            _sync_directory(directory)
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise
    raise RuntimeError("could not reserve a unique batch report path")


def checkpoint_batch_report(path: Path, content: str, *,
                            expected_sha256: str) -> str:
    """Atomically replace a report while retaining its last complete checkpoint."""
    path = Path(path)
    lock_path = path.parent / f".{path.name}.lock"
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if not path.is_file() or path.is_symlink():
                raise ValueError(
                    f"batch report must be an existing regular file: {path}")
            current_sha256 = report_sha256(path)
            if current_sha256 != expected_sha256:
                raise BatchReportConflict(
                    "batch report changed: expected "
                    f"{expected_sha256}, found {current_sha256}")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                _sync_directory(path.parent)
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
            return report_sha256(path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
