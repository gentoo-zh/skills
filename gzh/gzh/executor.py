from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from portage.dep import Atom, InvalidAtom

from gzh.executor_evidence import (
    MAX_ELOG_BYTES,
    MAX_ELOG_FILES,
    MAX_ELOG_TOTAL_BYTES,
    MAX_FINAL_LOG_BYTES,
    command_record,
    create_evidence,
)
from gzh.repo import github_slug


CONFIG_VERSION = 1
MAX_PATCH_BYTES = 16 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_COLLECTION_ERROR_PREVIEW_BYTES = 4096
CANONICAL_REPOSITORY = "gentoo-zh/overlay"
PORTAGE_ENVIRONMENT = {
    "PORTAGE_ELOG_CLASSES": "qa warn error",
    "PORTAGE_ELOG_SYSTEM": "save",
}
_SAFE_REMOTE_ENVIRONMENT = frozenset({
    "PORTAGE_ELOG_CLASSES", "PORTAGE_ELOG_SYSTEM", "PORTAGE_LOGDIR",
})
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}\Z")
_USER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}\Z")
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_ARCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_PROFILE_RE = re.compile(r"[A-Za-z0-9/][A-Za-z0-9_+./:-]{0,511}\Z")
MAX_REMOTE_ELOG_TOTAL_BYTES = 1024 * 1024
_REMOTE_ELOG_COLLECTOR = r"""
import base64
import json
import os
import stat
import sys

path, file_limit, byte_limit, total_limit = sys.argv[1:]
file_limit = int(file_limit)
byte_limit = int(byte_limit)
total_limit = int(total_limit)
flags = os.O_RDONLY
for flag_name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
    flags |= getattr(os, flag_name, 0)
directory_fd = os.open(path, flags)
try:
    directory_before = os.fstat(directory_fd)
    if not stat.S_ISDIR(directory_before.st_mode):
        raise RuntimeError("elog path is not a directory")
    names = sorted(os.listdir(directory_fd))
    if len(names) > file_limit:
        raise RuntimeError("saved elog evidence exceeds the bounded inventory")
    result = {}
    total = 0
    for name in names:
        if not name or "/" in name or name in {".", ".."}:
            raise RuntimeError("remote elog inventory contains an unsafe name")
        expected = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(expected.st_mode):
            raise RuntimeError("remote elog inventory contains a non-regular entry")
        if expected.st_size > byte_limit or total + expected.st_size > total_limit:
            raise RuntimeError("saved elog evidence exceeds the bounded inventory")
        file_flags = os.O_RDONLY
        for flag_name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            file_flags |= getattr(os, flag_name, 0)
        descriptor = os.open(name, file_flags, dir_fd=directory_fd)
        try:
            opened = os.fstat(descriptor)
            expected_identity = (
                expected.st_dev, expected.st_ino, expected.st_size,
                expected.st_mtime_ns, expected.st_ctime_ns,
            )
            opened_identity = (
                opened.st_dev, opened.st_ino, opened.st_size,
                opened.st_mtime_ns, opened.st_ctime_ns,
            )
            if not stat.S_ISREG(opened.st_mode) or opened_identity != expected_identity:
                raise RuntimeError("remote elog changed before it was opened")
            chunks = []
            remaining = byte_limit + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            observed = os.fstat(descriptor)
            observed_identity = (
                observed.st_dev, observed.st_ino, observed.st_size,
                observed.st_mtime_ns, observed.st_ctime_ns,
            )
            if observed_identity != opened_identity or len(content) != opened.st_size:
                raise RuntimeError("remote elog changed while evidence was collected")
        finally:
            os.close(descriptor)
        total += len(content)
        result[name] = base64.b64encode(content).decode("ascii")
    directory_after = os.fstat(directory_fd)
    if (sorted(os.listdir(directory_fd)) != names
            or directory_after.st_mtime_ns != directory_before.st_mtime_ns
            or directory_after.st_ctime_ns != directory_before.st_ctime_ns):
        raise RuntimeError("remote elog directory changed during collection")
    print(json.dumps({"files": result}, sort_keys=True, separators=(",", ":")))
finally:
    os.close(directory_fd)
""".strip()


class ExecutorError(RuntimeError):
    pass


class ExecutorConfigError(ExecutorError):
    pass


class ExecutorValidationError(ExecutorError):
    pass


class ExecutorAuthorizationError(ExecutorError):
    pass


@dataclass(frozen=True)
class ExecutorSpec:
    name: str
    type: str
    allow_dependency_install: bool
    host: str | None = None
    user: str | None = None
    port: int | None = None
    identity_file: Path | None = None
    remote_overlay_path: PurePosixPath | None = None


@dataclass(frozen=True)
class OwnedTransfer:
    commit: str
    parent: str
    paths: tuple[str, ...]
    patch: Path
    sha256: str
    size: int


@dataclass(frozen=True)
class InstallRequest:
    atom: str
    commit: str
    evidence_dir: Path
    use_state: tuple[str, ...] = ()
    transfer: OwnedTransfer | None = None


class RemoteTransport(Protocol):
    def run(
        self, argv: Sequence[str], *, cwd: PurePosixPath | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess: ...

    def upload_file(self, source: Path, destination: PurePosixPath) -> None: ...


def _require_keys(
    value: Mapping[str, Any], allowed: set[str], required: set[str], label: str,
) -> None:
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise ExecutorConfigError(f"unknown {label} fields: {', '.join(unknown)}")
    if missing:
        raise ExecutorConfigError(f"missing {label} fields: {', '.join(missing)}")


def _executor_name(value: object) -> str:
    if (not isinstance(value, str) or not _NAME_RE.fullmatch(value)
            or value in {".", ".."}):
        raise ExecutorConfigError(
            "executor names must contain 1-64 ASCII letters, digits, dots, "
            "underscores, or hyphens"
        )
    return value


def _absolute_remote_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ExecutorConfigError("remote_overlay_path must be an absolute POSIX path")
    path = PurePosixPath(value)
    if (not path.is_absolute() or str(path) != value
            or path == PurePosixPath("/")):
        raise ExecutorConfigError(
            "remote_overlay_path must be a normalized, non-root absolute POSIX path"
        )
    synced_root = PurePosixPath("/var/db/repos")
    if path == synced_root or synced_root in path.parents:
        raise ExecutorConfigError(
            "remote_overlay_path must not select a Portage-synced repository"
        )
    return path


def load_executor_config(path: Path) -> dict[str, ExecutorSpec]:
    """Load the strict, versioned executor table from a caller-owned TOML file."""
    config_path = Path(path).expanduser()
    try:
        value = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ExecutorConfigError(f"cannot read executor configuration: {exc}") from exc
    if not isinstance(value, dict):
        raise ExecutorConfigError("executor configuration must be a TOML table")
    _require_keys(value, {"version", "executors"}, {"version", "executors"}, "top-level")
    if value["version"] != CONFIG_VERSION or isinstance(value["version"], bool):
        raise ExecutorConfigError(f"executor configuration version must be {CONFIG_VERSION}")
    tables = value["executors"]
    if not isinstance(tables, dict) or not tables:
        raise ExecutorConfigError("executors must be a non-empty table")

    specs: dict[str, ExecutorSpec] = {}
    for raw_name, table in tables.items():
        name = _executor_name(raw_name)
        if not isinstance(table, dict):
            raise ExecutorConfigError(f"executor {name} must be a table")
        executor_type = table.get("type")
        common = {"type", "allow_dependency_install"}
        if executor_type == "local":
            _require_keys(table, common, common, f"executor {name}")
            if not isinstance(table["allow_dependency_install"], bool):
                raise ExecutorConfigError(
                    f"executor {name}.allow_dependency_install must be boolean"
                )
            specs[name] = ExecutorSpec(
                name=name, type="local",
                allow_dependency_install=table["allow_dependency_install"],
            )
            continue
        if executor_type != "ssh":
            raise ExecutorConfigError(f"executor {name}.type must be local or ssh")
        required = common | {
            "host", "user", "port", "identity_file", "remote_overlay_path",
        }
        _require_keys(table, required, required, f"executor {name}")
        host = table["host"]
        user = table["user"]
        port = table["port"]
        identity = table["identity_file"]
        authorization = table["allow_dependency_install"]
        if not isinstance(host, str) or not _HOST_RE.fullmatch(host):
            raise ExecutorConfigError(f"executor {name}.host is invalid")
        if not isinstance(user, str) or not _USER_RE.fullmatch(user):
            raise ExecutorConfigError(f"executor {name}.user is invalid")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ExecutorConfigError(f"executor {name}.port must be between 1 and 65535")
        if (not isinstance(identity, str) or any(ord(char) < 32 for char in identity)
                or not Path(identity).expanduser().is_absolute()):
            raise ExecutorConfigError(
                f"executor {name}.identity_file must be an absolute path"
            )
        if not isinstance(authorization, bool):
            raise ExecutorConfigError(
                f"executor {name}.allow_dependency_install must be boolean"
            )
        specs[name] = ExecutorSpec(
            name=name,
            type="ssh",
            allow_dependency_install=authorization,
            host=host,
            user=user,
            port=port,
            identity_file=Path(identity).expanduser(),
            remote_overlay_path=_absolute_remote_path(table["remote_overlay_path"]),
        )
    return specs


def validate_exact_atom(value: str) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or any(character.isspace() or ord(character) < 32 for character in value)):
        raise ValueError("atom must not contain whitespace or control characters")
    try:
        atom = Atom(value, allow_repo=True)
    except InvalidAtom as exc:
        raise ValueError(f"invalid package atom: {value}") from exc
    if atom.operator != "=" or atom.version is None or atom.blocker or atom.use:
        raise ValueError("executor requires an exact, non-blocking package atom")
    return value


def merge_argv(atom: str, *, onlydeps: bool) -> list[str]:
    atom = validate_exact_atom(atom)
    argv = ["emerge", "--usepkg=n"]
    if onlydeps:
        return [*argv, "--onlydeps", atom]
    return [*argv, "--oneshot", "--selective=n", atom]


def quote_posix(value: object) -> str:
    """Always quote one argument for a POSIX-compatible remote login shell."""
    text = str(value)
    if "\x00" in text:
        raise ValueError("remote arguments must not contain NUL bytes")
    return "'" + text.replace("'", "'\"'\"'") + "'"


def build_remote_command(
    argv: Sequence[object], *, cwd: PurePosixPath | None = None,
    environment: Mapping[str, object] | None = None,
) -> str:
    if not argv:
        raise ValueError("remote command must not be empty")
    assignments: list[str] = []
    for key, raw_value in sorted((environment or {}).items()):
        if key not in _SAFE_REMOTE_ENVIRONMENT:
            raise ValueError(f"remote environment key is not allowed: {key}")
        assignments.append(f"{key}={quote_posix(raw_value)}")
    command = " ".join(quote_posix(value) for value in argv)
    if assignments:
        command = f"'env' {' '.join(assignments)} {command}"
    command = f"exec {command}"
    if cwd is not None:
        if not cwd.is_absolute() or cwd == PurePosixPath("/"):
            raise ValueError("remote command cwd must be a non-root absolute path")
        command = f"cd {quote_posix(cwd)} && {command}"
    return command


def _output(proc: subprocess.CompletedProcess, stream: str) -> str:
    value = getattr(proc, stream, "")
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _bounded_output(proc: subprocess.CompletedProcess) -> subprocess.CompletedProcess:
    stdout = _output(proc, "stdout")
    stderr = _output(proc, "stderr")
    combined = len(stdout.encode()) + len(stderr.encode())
    if combined <= MAX_COMMAND_OUTPUT_BYTES:
        return subprocess.CompletedProcess(
            proc.args, proc.returncode, stdout=stdout, stderr=stderr,
        )
    remaining = MAX_COMMAND_OUTPUT_BYTES
    stdout_bytes = stdout.encode()[:remaining]
    remaining -= len(stdout_bytes)
    stderr_bytes = stderr.encode()[:remaining]
    return subprocess.CompletedProcess(
        proc.args, 125,
        stdout=stdout_bytes.decode(errors="replace"),
        stderr=(stderr_bytes.decode(errors="replace")
                + "\ngzh: command output limit exceeded\n"),
    )


class SSHTransport:
    """OpenSSH transport that never invokes a local shell or forwards caller secrets."""

    def __init__(
        self, spec: ExecutorSpec, *, runner: Callable = subprocess.run,
        timeout: int = 24 * 60 * 60,
    ) -> None:
        if spec.type != "ssh":
            raise ExecutorConfigError("SSHTransport requires an ssh executor spec")
        if spec.identity_file is None or not spec.identity_file.is_absolute():
            raise ExecutorConfigError("SSH identity_file must be absolute")
        self.spec = spec
        self.runner = runner
        self.timeout = timeout

    def _connection_options(self, program: str) -> list[str]:
        assert self.spec.port is not None
        assert self.spec.identity_file is not None
        return [
            "-F", "/dev/null",
            "-o", "ClearAllForwardings=yes",
            "-o", "IdentitiesOnly=yes",
            "-o", "IdentityAgent=none",
            "-o", "PermitLocalCommand=no",
            "-i", str(self.spec.identity_file),
            "-p" if program == "ssh" else "-P", str(self.spec.port),
        ]

    @staticmethod
    def _local_environment() -> dict[str, str]:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        if home := os.environ.get("HOME"):
            environment["HOME"] = home
        return environment

    def run(
        self, argv: Sequence[str], *, cwd: PurePosixPath | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        remote = build_remote_command(argv, cwd=cwd, environment=environment)
        assert self.spec.host is not None
        assert self.spec.user is not None
        command = [
            "ssh", *self._connection_options("ssh"),
            "--", f"{self.spec.user}@{self.spec.host}", remote,
        ]
        proc = self.runner(
            command, capture_output=True, text=True, timeout=self.timeout,
            env=self._local_environment(),
        )
        return _bounded_output(proc)

    def upload_file(self, source: Path, destination: PurePosixPath) -> None:
        if not source.is_file() or source.is_symlink():
            raise ExecutorValidationError(f"transfer source is not a regular file: {source}")
        assert self.spec.host is not None
        assert self.spec.user is not None
        command = [
            "scp", *self._connection_options("scp"), "--", str(source),
            f"{self.spec.user}@{self.spec.host}:{destination}",
        ]
        proc = self.runner(
            command, capture_output=True, text=True, timeout=self.timeout,
            env=self._local_environment(),
        )
        if proc.returncode != 0:
            raise ExecutorError(f"owned patch transfer failed: {_output(proc, 'stderr').strip()}")


def _git_run(
    runner: Callable, argv: Sequence[str], *, cwd: Path, text: bool = True,
) -> subprocess.CompletedProcess:
    return runner(
        list(argv), cwd=str(cwd), capture_output=True, text=text, timeout=60,
    )


def _owned_path(value: str) -> str:
    if (not isinstance(value, str) or not value
            or any(ord(character) < 32 for character in value)):
        raise ExecutorValidationError("owned transfer paths must be non-empty strings")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or path == PurePosixPath("."):
        raise ExecutorValidationError(f"unsafe owned transfer path: {value!r}")
    return value


def create_commit_patch(
    repository: Path,
    commit: str,
    owned_paths: Sequence[str],
    destination: Path,
    *,
    runner: Callable = subprocess.run,
) -> OwnedTransfer:
    """Create one bounded patch containing every and only path changed by a commit."""
    root = Path(repository).expanduser().resolve()
    requested = tuple(sorted({_owned_path(value) for value in owned_paths}))
    if not requested:
        raise ExecutorValidationError("at least one owned transfer path is required")
    resolved_proc = _git_run(
        runner, ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=root,
    )
    resolved = _output(resolved_proc, "stdout").strip()
    if resolved_proc.returncode != 0 or not _COMMIT_RE.fullmatch(resolved):
        raise ExecutorValidationError("cannot resolve the transfer commit")
    parents_proc = _git_run(
        runner, ["git", "rev-list", "--parents", "-n", "1", resolved], cwd=root,
    )
    parents = _output(parents_proc, "stdout").split()
    if (parents_proc.returncode != 0 or len(parents) != 2
            or parents[0] != resolved or not _COMMIT_RE.fullmatch(parents[1])):
        raise ExecutorValidationError("transfer commit must have exactly one parent")
    parent = parents[1]
    names_proc = _git_run(
        runner,
        ["git", "diff-tree", "-z", "--no-commit-id", "--name-only", "-r", resolved],
        cwd=root,
    )
    changed = tuple(sorted(filter(None, _output(names_proc, "stdout").split("\x00"))))
    if names_proc.returncode != 0 or changed != requested:
        raise ExecutorValidationError(
            "owned transfer paths must exactly match every path changed by the commit"
        )
    patch_proc = _git_run(
        runner,
        ["git", "diff", "--binary", "--full-index", parent, resolved, "--", *requested],
        cwd=root,
        text=False,
    )
    raw_patch = patch_proc.stdout or b""
    content = raw_patch.encode() if isinstance(raw_patch, str) else bytes(raw_patch)
    if patch_proc.returncode != 0 or not content:
        raise ExecutorValidationError("cannot create an owned patch for the commit")
    if len(content) > MAX_PATCH_BYTES:
        raise ExecutorValidationError(
            f"owned patch exceeds the {MAX_PATCH_BYTES}-byte transfer limit"
        )
    requested_output = Path(destination).expanduser()
    if requested_output.exists() or requested_output.is_symlink():
        raise ExecutorValidationError(
            f"patch destination already exists: {requested_output}")
    output = requested_output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise ExecutorValidationError(
            f"patch destination already exists: {requested_output}") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return OwnedTransfer(
        commit=resolved,
        parent=parent,
        paths=requested,
        patch=output,
        sha256=hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def _checked_result(
    proc: subprocess.CompletedProcess, label: str,
) -> subprocess.CompletedProcess:
    if proc.returncode != 0:
        detail = _output(proc, "stderr").strip() or _output(proc, "stdout").strip()
        raise ExecutorValidationError(f"{label} failed: {detail}")
    return proc


def validate_remote_repository(
    spec: ExecutorSpec, transport: RemoteTransport,
) -> dict[str, Any]:
    """Bind a configured path to Portage and to the canonical Git repository."""
    if spec.type != "ssh" or spec.remote_overlay_path is None:
        raise ExecutorConfigError("remote repository validation requires an ssh executor")
    portageq = _checked_result(
        transport.run(["portageq", "get_repo_path", "/", "gentoo-zh"]),
        "portage repository lookup",
    )
    discovered = PurePosixPath(_output(portageq, "stdout").strip())
    if discovered != spec.remote_overlay_path:
        raise ExecutorValidationError(
            "configured remote overlay path does not match portageq get_repo_path"
        )
    root_proc = _checked_result(
        transport.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=spec.remote_overlay_path,
        ),
        "remote Git root lookup",
    )
    git_root = PurePosixPath(_output(root_proc, "stdout").strip())
    if git_root != spec.remote_overlay_path:
        raise ExecutorValidationError("configured remote overlay is not its Git worktree root")
    remotes_proc = _checked_result(
        transport.run(["git", "remote", "-v"], cwd=spec.remote_overlay_path),
        "remote Git identity lookup",
    )
    canonical_urls: list[str] = []
    for line in _output(remotes_proc, "stdout").splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[-1] == "(fetch)":
            if github_slug(fields[1]) == CANONICAL_REPOSITORY:
                canonical_urls.append(fields[1])
    if not canonical_urls:
        raise ExecutorValidationError(
            f"remote overlay has no fetch remote for {CANONICAL_REPOSITORY}"
        )
    status_proc = _checked_result(
        transport.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=spec.remote_overlay_path,
        ),
        "remote Git status lookup",
    )
    if _output(status_proc, "stdout"):
        raise ExecutorValidationError(
            "remote overlay worktree must be clean before owned patch transfer"
        )
    head_proc = _checked_result(
        transport.run(["git", "rev-parse", "HEAD"], cwd=spec.remote_overlay_path),
        "remote Git HEAD lookup",
    )
    head = _output(head_proc, "stdout").strip()
    if not _COMMIT_RE.fullmatch(head):
        raise ExecutorValidationError("remote Git HEAD is not a full commit identifier")
    return {
        "path": str(spec.remote_overlay_path),
        "git_root": str(git_root),
        "canonical_urls": sorted(set(canonical_urls)),
        "clean": True,
        "head": head,
    }


def _lines(proc: subprocess.CompletedProcess) -> list[str]:
    return sorted({line for line in _output(proc, "stdout").splitlines() if line})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Executor:
    type = "local"

    def __init__(self, spec: ExecutorSpec, *, runner: Callable = subprocess.run,
                 timeout: int = 24 * 60 * 60) -> None:
        self.spec = spec
        self.runner = runner
        self.timeout = timeout

    def _run(
        self, argv: Sequence[str], *, environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env.update(environment or {})
        proc = self.runner(
            list(argv), capture_output=True, text=True, env=env,
            timeout=self.timeout,
        )
        return _bounded_output(proc)

    def _make_run_dir(self) -> Path:
        return Path(tempfile.mkdtemp(prefix="gzh-executor-")).resolve()

    def _collect_elogs(self, elog_dir: Path) -> dict[str, bytes]:
        directory_fd: int | None = None
        try:
            expected_directory = elog_dir.lstat()
            if not stat.S_ISDIR(expected_directory.st_mode):
                raise ExecutorError("saved elog path is not a regular directory")
            flags = os.O_RDONLY
            for flag_name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
                flags |= getattr(os, flag_name, 0)
            directory_fd = os.open(elog_dir, flags)
            opened_directory = os.fstat(directory_fd)
            if ((opened_directory.st_dev, opened_directory.st_ino)
                    != (expected_directory.st_dev, expected_directory.st_ino)):
                raise ExecutorError("saved elog directory changed before collection")
            names = sorted(os.listdir(directory_fd))
            if len(names) > MAX_ELOG_FILES:
                raise ExecutorError(
                    "saved elog evidence exceeds the bounded inventory")
            result: dict[str, bytes] = {}
            total = 0
            for name in names:
                expected = os.stat(
                    name, dir_fd=directory_fd, follow_symlinks=False)
                if not stat.S_ISREG(expected.st_mode):
                    raise ExecutorError(
                        "saved elog inventory contains a non-regular entry")
                if expected.st_size > MAX_ELOG_BYTES:
                    raise ExecutorError(
                        "saved elog evidence exceeds the per-file limit")
                total += expected.st_size
                if total > MAX_ELOG_TOTAL_BYTES:
                    raise ExecutorError(
                        "saved elog evidence exceeds the aggregate limit")
                file_flags = os.O_RDONLY
                for flag_name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
                    file_flags |= getattr(os, flag_name, 0)
                descriptor = os.open(name, file_flags, dir_fd=directory_fd)
                try:
                    opened = os.fstat(descriptor)
                    expected_identity = (
                        expected.st_dev, expected.st_ino, expected.st_size,
                        expected.st_mtime_ns, expected.st_ctime_ns,
                    )
                    opened_identity = (
                        opened.st_dev, opened.st_ino, opened.st_size,
                        opened.st_mtime_ns, opened.st_ctime_ns,
                    )
                    if (not stat.S_ISREG(opened.st_mode)
                            or opened_identity != expected_identity):
                        raise ExecutorError(
                            "saved elog changed before it was opened")
                    chunks: list[bytes] = []
                    remaining = MAX_ELOG_BYTES + 1
                    while remaining > 0:
                        chunk = os.read(descriptor, min(65536, remaining))
                        if not chunk:
                            break
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    content = b"".join(chunks)
                    observed = os.fstat(descriptor)
                    observed_identity = (
                        observed.st_dev, observed.st_ino, observed.st_size,
                        observed.st_mtime_ns, observed.st_ctime_ns,
                    )
                    if (observed_identity != opened_identity
                            or len(content) != opened.st_size):
                        raise ExecutorError(
                            "saved elog changed while evidence was collected")
                finally:
                    os.close(descriptor)
                result[name] = content
            final_directory = os.fstat(directory_fd)
            if (sorted(os.listdir(directory_fd)) != names
                    or final_directory.st_mtime_ns != opened_directory.st_mtime_ns
                    or final_directory.st_ctime_ns != opened_directory.st_ctime_ns):
                raise ExecutorError(
                    "saved elog directory changed during collection")
            return result
        except OSError as exc:
            raise ExecutorError(f"cannot collect saved elog evidence: {exc}") from exc
        finally:
            if directory_fd is not None:
                os.close(directory_fd)

    def execute(self, request: InstallRequest) -> dict[str, Any]:
        return _execute_install(self, request)


class LocalExecutor(Executor):
    type = "local"

    def __init__(self, spec: ExecutorSpec, *, runner: Callable = subprocess.run) -> None:
        if spec.type != "local":
            raise ExecutorConfigError("LocalExecutor requires a local executor spec")
        super().__init__(spec, runner=runner)


class SSHExecutor(Executor):
    type = "ssh"

    def __init__(
        self, spec: ExecutorSpec, *, transport: RemoteTransport | None = None,
        runner: Callable = subprocess.run,
    ) -> None:
        if spec.type != "ssh" or spec.remote_overlay_path is None:
            raise ExecutorConfigError("SSHExecutor requires an ssh executor spec")
        super().__init__(spec, runner=runner)
        self.transport = transport or SSHTransport(spec, runner=runner)
        self._remote_run_dir: PurePosixPath | None = None
        self._remote_patch: PurePosixPath | None = None
        self._patch_applied = False

    def _run(
        self, argv: Sequence[str], *, environment: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        return self.transport.run(
            argv, cwd=self.spec.remote_overlay_path, environment=environment,
        )

    def _make_run_dir(self) -> PurePosixPath:
        transfer = getattr(self, "_active_transfer", None)
        if transfer is None:
            raise ExecutorValidationError("ssh execution requires an owned commit patch")
        validation = validate_remote_repository(self.spec, self.transport)
        self._remote_validation = validation
        if validation["head"] != transfer.parent:
            raise ExecutorValidationError(
                "remote Git HEAD does not match the transferred commit parent"
            )
        run_dir = PurePosixPath(f"/tmp/gzh-executor-{uuid.uuid4().hex}")
        _checked_result(
            self.transport.run(["mkdir", "-m", "700", "--", str(run_dir)]),
            "remote run directory creation",
        )
        remote_patch = run_dir / "owned.patch"
        self._remote_run_dir = run_dir
        self._remote_patch = remote_patch
        self.transport.upload_file(transfer.patch, remote_patch)
        digest_proc = _checked_result(
            self.transport.run(["sha256sum", "--", str(remote_patch)]),
            "remote patch digest",
        )
        if _output(digest_proc, "stdout").split(maxsplit=1)[0] != transfer.sha256:
            raise ExecutorValidationError("remote patch digest does not match local evidence")
        _checked_result(
            self.transport.run(
                ["git", "apply", "--check", str(remote_patch)],
                cwd=self.spec.remote_overlay_path,
            ),
            "remote owned patch applicability",
        )
        _checked_result(
            self.transport.run(
                ["git", "apply", str(remote_patch)], cwd=self.spec.remote_overlay_path,
            ),
            "remote owned patch application",
        )
        self._patch_applied = True
        return run_dir

    def _collect_elogs(self, elog_dir: PurePosixPath) -> dict[str, bytes]:
        command = [
            "python3", "-c", _REMOTE_ELOG_COLLECTOR, str(elog_dir),
            str(MAX_ELOG_FILES), str(MAX_ELOG_BYTES),
            str(MAX_REMOTE_ELOG_TOTAL_BYTES),
        ]
        collected = _checked_result(
            self.transport.run(command), "remote elog collection")
        try:
            payload = json.loads(_output(collected, "stdout"))
        except json.JSONDecodeError as exc:
            raise ExecutorError("remote elog evidence is not valid JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"files"} \
                or not isinstance(payload["files"], dict):
            raise ExecutorError("remote elog evidence schema is invalid")
        if len(payload["files"]) > MAX_ELOG_FILES:
            raise ExecutorError("saved elog evidence exceeds the bounded inventory")
        result: dict[str, bytes] = {}
        total = 0
        for name, encoded in payload["files"].items():
            if (not isinstance(name, str) or PurePosixPath(name).name != name
                    or name in {".", ".."} or not isinstance(encoded, str)):
                raise ExecutorError("remote elog evidence schema is invalid")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ExecutorError("remote elog content encoding is invalid") from exc
            total += len(content)
            if (len(content) > MAX_ELOG_BYTES
                    or total > MAX_REMOTE_ELOG_TOTAL_BYTES):
                raise ExecutorError("saved elog evidence exceeds the bounded inventory")
            result[name] = content
        return result

    def _cleanup(self) -> dict[str, Any]:
        commands: list[list[str]] = []
        errors: list[str] = []
        restored = False
        if self._patch_applied and self._remote_patch is not None:
            reverse_check = ["git", "apply", "--reverse", "--check", str(self._remote_patch)]
            commands.append(reverse_check)
            proc = self.transport.run(reverse_check, cwd=self.spec.remote_overlay_path)
            if proc.returncode == 0:
                reverse = ["git", "apply", "--reverse", str(self._remote_patch)]
                commands.append(reverse)
                proc = self.transport.run(reverse, cwd=self.spec.remote_overlay_path)
            if proc.returncode != 0:
                errors.append("owned patch restoration failed")
            else:
                restored = True
        if self._remote_run_dir is not None and not errors:
            remove = ["rm", "-rf", "--", str(self._remote_run_dir)]
            commands.append(remove)
            proc = self.transport.run(remove)
            if proc.returncode != 0:
                errors.append("owned remote run directory cleanup failed")
        return {
            "ok": not errors,
            "errors": errors,
            "commands": [command_record(command) for command in commands],
            "restored_paths": (list(getattr(self, "_active_transfer").paths)
                               if restored else []),
            "removed_paths": ([str(self._remote_run_dir)]
                              if self._remote_run_dir is not None and not errors else []),
            "retained_paths": ([str(self._remote_run_dir)]
                               if self._remote_run_dir is not None and errors else []),
        }

    def execute(self, request: InstallRequest) -> dict[str, Any]:
        if request.transfer is None or request.transfer.commit != request.commit:
            raise ExecutorValidationError(
                "ssh execution requires an owned patch tied to the evidence commit"
            )
        self._active_transfer = request.transfer
        return _execute_install(self, request)


def _execution_environment(logdir: Path | PurePosixPath) -> dict[str, str]:
    return {**PORTAGE_ENVIRONMENT, "PORTAGE_LOGDIR": str(logdir)}


def _final_log(
    dependencies: subprocess.CompletedProcess | None,
    merge: subprocess.CompletedProcess | None,
) -> tuple[str, bool]:
    sections: list[str] = []
    for label, proc in (("onlydeps", dependencies), ("merge", merge)):
        if proc is None:
            continue
        sections.append(
            f"[{label} stdout]\n{_output(proc, 'stdout')}\n"
            f"[{label} stderr]\n{_output(proc, 'stderr')}"
        )
    text = "\n".join(sections)
    content = text.encode()
    if len(content) <= MAX_FINAL_LOG_BYTES:
        return text, False
    marker = b"\ngzh: final log truncated\n"
    bounded = content[:MAX_FINAL_LOG_BYTES - len(marker)] + marker
    return bounded.decode(errors="replace"), True


def _collection_error(exc: ExecutorError) -> dict[str, Any]:
    content = str(exc).encode()
    preview = content[:MAX_COLLECTION_ERROR_PREVIEW_BYTES]
    return {
        "type": type(exc).__name__,
        "message": preview.decode(errors="replace"),
        "message_bytes": len(content),
        "message_sha256": hashlib.sha256(content).hexdigest(),
        "truncated": len(content) > len(preview),
    }


def _environment_value(
        proc: subprocess.CompletedProcess, pattern: re.Pattern[str],
) -> str | None:
    if proc.returncode != 0:
        return None
    value = _output(proc, "stdout").strip()
    if not value or "\n" in value or "\r" in value or not pattern.fullmatch(value):
        return None
    return value


def _execute_install(executor: Executor, request: InstallRequest) -> dict[str, Any]:
    atom = validate_exact_atom(request.atom)
    if not _COMMIT_RE.fullmatch(request.commit):
        raise ExecutorValidationError("evidence commit must be a full commit identifier")
    if not executor.spec.allow_dependency_install:
        raise ExecutorAuthorizationError(
            f"executor {executor.spec.name} is not authorized to install dependencies"
        )
    requested_evidence = Path(request.evidence_dir).expanduser()
    if requested_evidence.exists() or requested_evidence.is_symlink():
        raise ExecutorValidationError(
            f"evidence directory already exists: {requested_evidence}")
    evidence_directory = requested_evidence.resolve()
    started_at = _utc_now()
    commands: list[dict[str, Any]] = []
    run_dir: Path | PurePosixPath | None = None
    merge: subprocess.CompletedProcess | None = None
    dependency_proc: subprocess.CompletedProcess | None = None
    elogs: dict[str, bytes] = {}
    before: list[str] = []
    after_dependencies: list[str] = []
    installed: list[str] = []
    arch = "unknown"
    profile = "unknown"
    failed_step: str | None = None
    collection_errors: list[dict[str, Any]] = []
    cleanup: dict[str, Any] = {"ok": True, "errors": [], "removed_paths": []}
    try:
        run_dir = executor._make_run_dir()
        logdir = run_dir / "logs"
        elog_dir = logdir / "elog"
        mkdir = ["mkdir", "-p", "--", str(elog_dir)]
        commands.append(command_record(mkdir))
        if executor.type == "local":
            Path(elog_dir).mkdir(parents=True, exist_ok=True)
        else:
            _checked_result(executor.transport.run(mkdir), "remote elog directory creation")

        environment = _execution_environment(logdir)
        arch_command = ["portageq", "envvar", "ARCH"]
        profile_command = ["eselect", "--brief", "profile", "show"]
        inventory_command = ["qlist", "-IC"]
        for command in (arch_command, profile_command, inventory_command):
            commands.append(command_record(command))
        arch_proc = executor._run(arch_command)
        profile_proc = executor._run(profile_command)
        before_proc = executor._run(inventory_command)
        arch_value = _environment_value(arch_proc, _ARCH_RE)
        profile_value = _environment_value(profile_proc, _PROFILE_RE)
        if arch_value is not None:
            arch = arch_value
        if profile_value is not None:
            profile = profile_value
        before = _lines(before_proc) if before_proc.returncode == 0 else []

        if (arch == "unknown" or profile == "unknown"
                or before_proc.returncode != 0):
            failed_step = "preflight"
        else:
            dependencies = merge_argv(atom, onlydeps=True)
            commands.append(command_record(dependencies, environment=environment))
            dependency_proc = executor._run(dependencies, environment=environment)
            if dependency_proc.returncode != 0:
                failed_step = "onlydeps"
            else:
                after_proc = executor._run(inventory_command)
                after_dependencies = _lines(after_proc) if after_proc.returncode == 0 else []
                if after_proc.returncode != 0:
                    failed_step = "evidence"
                else:
                    try:
                        elogs = executor._collect_elogs(elog_dir)
                    except ExecutorError as exc:
                        collection_errors.append(_collection_error(exc))
                        failed_step = "evidence"
                    if failed_step is None and elogs:
                        failed_step = "elog"
                if failed_step is None:
                    merge_command = merge_argv(atom, onlydeps=False)
                    commands.append(command_record(merge_command, environment=environment))
                    merge = executor._run(merge_command, environment=environment)
                    try:
                        elogs = executor._collect_elogs(elog_dir)
                    except ExecutorError as exc:
                        collection_errors.append(_collection_error(exc))
                        failed_step = "evidence"
                    if failed_step is None and merge.returncode != 0:
                        failed_step = "merge"
                    elif failed_step is None and elogs:
                        failed_step = "elog"
                    installed_command = ["qlist", "-e", atom]
                    commands.append(command_record(installed_command))
                    installed_proc = executor._run(installed_command)
                    installed = _lines(installed_proc) if installed_proc.returncode == 0 else []
                    if installed_proc.returncode != 0 and failed_step is None:
                        failed_step = "evidence"
    except Exception:
        if isinstance(executor, SSHExecutor):
            cleanup = executor._cleanup()
        elif run_dir is not None:
            shutil.rmtree(run_dir)
        raise
    else:
        if isinstance(executor, SSHExecutor):
            cleanup = executor._cleanup()
        elif run_dir is not None:
            shutil.rmtree(run_dir)
            cleanup = {
                "ok": not Path(run_dir).exists(),
                "errors": [],
                "removed_paths": [str(run_dir)],
                "restored_paths": [],
            }
    if not cleanup.get("ok", False):
        failed_step = "cleanup"
    final_log, truncated = _final_log(dependency_proc, merge)
    if truncated:
        failed_step = "evidence"
    retained = sorted(set(after_dependencies) - set(before))
    ended_at = _utc_now()
    provenance: dict[str, Any] = {}
    if request.transfer is not None:
        provenance.update({
            "remote_repository": getattr(executor, "_remote_validation", None),
            "owned_transfer": {
                "commit": request.transfer.commit,
                "parent": request.transfer.parent,
                "paths": list(request.transfer.paths),
                "sha256": request.transfer.sha256,
                "size": request.transfer.size,
            },
        })
    if collection_errors:
        provenance["collection_errors"] = collection_errors
    record = create_evidence(
        evidence_directory,
        executor_type=executor.type,
        executor_name=executor.spec.name,
        package=atom,
        commit=request.commit,
        commands=commands,
        started_at=started_at,
        ended_at=ended_at,
        exit_state=failed_step or "passed",
        use_state=request.use_state,
        arch=arch,
        profile=profile,
        final_log=final_log,
        elogs=elogs,
        installed_inventory=installed,
        cleanup=cleanup,
        retained_dependencies=retained,
        provenance=provenance,
    )
    record["ok"] = failed_step is None
    record["failed_step"] = failed_step
    return record


def create_executor(
    spec: ExecutorSpec,
    *,
    runner: Callable = subprocess.run,
    transport: RemoteTransport | None = None,
) -> Executor:
    if spec.type == "local":
        if transport is not None:
            raise ExecutorConfigError("local executors do not accept an SSH transport")
        return LocalExecutor(spec, runner=runner)
    if spec.type == "ssh":
        return SSHExecutor(spec, transport=transport, runner=runner)
    raise ExecutorConfigError(f"unknown executor type: {spec.type}")
