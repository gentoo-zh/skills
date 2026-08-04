from __future__ import annotations

import configparser
import errno
import hashlib
import io
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Mapping, Sequence

from portage.dep import Atom, InvalidAtom
from gzh.qa_evidence import (
    MAX_OUTPUT_BYTES,
    MAX_TIMEOUT,
    read_tool_version,
    run_evidence_command,
)
from gzh.repo import validate_overlay_root


MAX_ELOG_ENTRIES = 256
DEFAULT_VERIFY_TIMEOUT = 6 * 60 * 60
DEFAULT_VERIFY_MAX_OUTPUT_BYTES = MAX_OUTPUT_BYTES
_ARCH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}\Z")
_KEYWORDS_RE = re.compile(
    r"[A-Za-z0-9~+_.@*-]+(?:\s+[A-Za-z0-9~+_.@*-]+)*\Z")
_PROFILE_RE = re.compile(r"[A-Za-z0-9/][A-Za-z0-9_+./:-]{0,511}\Z")
_CP_RE = re.compile(
    r"[A-Za-z0-9+_.-]+/[A-Za-z0-9+_.-]+\Z")
_PLAN_MERGE_RE = re.compile(
    r"^\s*\[(?P<source>ebuild|binary)\s+(?P<flags>[^]]*)\]\s+"
    r"(?P<atom>\S+)")
_PLAN_ROW_RE = re.compile(r"^\s*\[(?P<operation>[A-Za-z][A-Za-z0-9,_-]*)\b")
_PLAN_TOTAL_RE = re.compile(r"^Total:\s+(?P<count>[0-9]+)\s+packages?\b")
_REPOSITORY_NAME = "gentoo-zh"
_NON_MUTATING_PLAN_ROWS = frozenset({"blocks", "nomerge"})


def atom_from_ebuild(ebuild: Path) -> str:
    ebuild = Path(ebuild).resolve()
    if ebuild.suffix != ".ebuild" or not ebuild.is_file() or len(ebuild.parents) < 3:
        raise ValueError(f"not an ebuild path: {ebuild}")
    package = ebuild.parent.name
    category = ebuild.parent.parent.name
    if not ebuild.name.startswith(f"{package}-"):
        raise ValueError(f"ebuild filename does not match its package: {ebuild}")
    try:
        root = validate_overlay_root(ebuild.parents[2])
    except RuntimeError as exc:
        raise ValueError(
            f"ebuild is not in a gentoo-zh development checkout: {ebuild}") from exc
    try:
        ebuild.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"ebuild is outside the overlay: {ebuild}") from exc
    return f"={category}/{ebuild.stem}::gentoo-zh"


def _environment_value(
        report: dict, *, pattern: re.Pattern[str],
) -> str | None:
    if (report.get("complete") is not True or report.get("truncated") is True
            or report.get("returncode") != 0):
        return None
    value = report["stdout"].strip()
    if not value or "\n" in value or "\r" in value or not pattern.fullmatch(value):
        return None
    return value


def _absolute_path_value(report: dict) -> Path | None:
    if (report.get("complete") is not True or report.get("truncated") is True
            or report.get("returncode") != 0):
        return None
    value = report["stdout"].strip()
    if (not value or "\n" in value or "\r" in value
            or any(ord(character) < 32 for character in value)):
        return None
    path = Path(value)
    if not path.is_absolute():
        return None
    return path.resolve()


def _pending_elog_inventory(elog_dir: Path) -> dict:
    return {
        "path": str(elog_dir.absolute()),
        "exists": elog_dir.exists(),
        "entries": [],
        "complete": False,
        "truncated": False,
        "state": "not-collected",
        "errors": [],
    }


def _read_regular_elog(
        directory_fd: int, name: str, path: Path,
        expected: os.stat_result, maximum: int,
) -> bytes:
    flags = os.O_RDONLY
    for flag_name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
        flags |= getattr(os, flag_name, 0)
    descriptor = os.open(name, flags, dir_fd=directory_fd)
    try:
        opened = os.fstat(descriptor)
        identity = (opened.st_dev, opened.st_ino)
        if (not stat.S_ISREG(opened.st_mode)
                or identity != (expected.st_dev, expected.st_ino)):
            raise OSError(errno.ESTALE, "elog entry changed before it was opened")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        observed = os.fstat(descriptor)
        if ((observed.st_dev, observed.st_ino, observed.st_size,
             observed.st_mtime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size,
                    opened.st_mtime_ns)):
            raise OSError(errno.ESTALE, "elog entry changed while it was read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _elog_inventory(elog_dir: Path, maximum: int) -> dict:
    inventory = _pending_elog_inventory(elog_dir)
    errors: list[str] = []
    truncated = False
    entries: list[dict] = []
    remaining = maximum
    directory_fd: int | None = None
    try:
        expected_directory = elog_dir.lstat()
        if not stat.S_ISDIR(expected_directory.st_mode):
            raise OSError(errno.ENOTDIR, "elog path is not a directory")
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW"):
            flags |= getattr(os, name, 0)
        directory_fd = os.open(elog_dir, flags)
        opened_directory = os.fstat(directory_fd)
        if ((opened_directory.st_dev, opened_directory.st_ino)
                != (expected_directory.st_dev, expected_directory.st_ino)):
            raise OSError(errno.ESTALE, "elog directory changed before it was opened")
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        inventory.update({
            "state": "incomplete",
            "errors": [f"cannot list elog directory: {exc}"],
        })
        if directory_fd is not None:
            os.close(directory_fd)
        return inventory
    inventory.update({"exists": True, "state": "complete"})

    if len(names) > MAX_ELOG_ENTRIES:
        names = names[:MAX_ELOG_ENTRIES]
        truncated = True
        errors.append(f"elog inventory exceeds {MAX_ELOG_ENTRIES} entries")

    for name in names:
        path = elog_dir / name
        entry = {
            "path": str(path),
            "kind": "unknown",
            "size": None,
            "sha256": None,
            "text": "",
            "truncated": False,
        }
        try:
            item_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISREG(item_stat.st_mode):
                entry["kind"] = "file"
                entry["size"] = item_stat.st_size
                content = _read_regular_elog(
                    directory_fd, name, path, item_stat, remaining)
                if len(content) > remaining or item_stat.st_size > remaining:
                    content = content[:remaining]
                    entry["truncated"] = True
                    truncated = True
                    errors.append(f"elog content exceeds evidence limit: {path}")
                else:
                    entry["sha256"] = hashlib.sha256(content).hexdigest()
                entry["text"] = content.decode(errors="replace")
                remaining -= len(content)
            elif stat.S_ISDIR(item_stat.st_mode):
                entry["kind"] = "directory"
                errors.append(f"unexpected directory in elog inventory: {path}")
            elif stat.S_ISLNK(item_stat.st_mode):
                entry["kind"] = "symlink"
                errors.append(f"unexpected symlink in elog inventory: {path}")
            else:
                entry["kind"] = "other"
                errors.append(f"unexpected special file in elog inventory: {path}")
        except OSError as exc:
            errors.append(f"cannot inspect elog entry {path}: {exc}")
        entries.append(entry)
    try:
        final_directory = os.fstat(directory_fd)
        if (sorted(os.listdir(directory_fd)) != names
                or final_directory.st_mtime_ns != opened_directory.st_mtime_ns
                or final_directory.st_ctime_ns != opened_directory.st_ctime_ns):
            errors.append("elog directory changed during inventory")
    except OSError as exc:
        errors.append(f"cannot recheck elog directory: {exc}")
    os.close(directory_fd)

    complete = not errors and not truncated
    inventory.update({
        "entries": entries,
        "complete": complete,
        "truncated": truncated,
        "state": "complete" if complete else "incomplete",
        "errors": errors,
    })
    return inventory


def _run_emerge(
        command: list[str], *, env: dict[str, str], timeout: int,
        max_output_bytes: int,
        runner: Callable[..., subprocess.CompletedProcess] | None,
) -> dict:
    return run_evidence_command(
        command, env=env, timeout=timeout, max_output_bytes=max_output_bytes,
        runner=runner)


def _step(name: str, execution: dict) -> dict:
    return {"name": name, **execution}


def _owned_repositories_config(
        current: str, overlay: Path, destination: Path,
) -> tuple[str, dict]:
    if any(ord(character) < 32 for character in str(overlay)):
        raise ValueError("overlay path contains control characters")
    parser = configparser.RawConfigParser(
        interpolation=None, strict=True, empty_lines_in_values=False)
    try:
        parser.read_string(current)
    except configparser.Error as exc:
        raise ValueError(f"Portage repository configuration is invalid: {exc}") from exc
    if not parser.sections() or not parser.defaults().get("main-repo"):
        raise ValueError("Portage repository configuration has no main repository")
    if not parser.has_section(_REPOSITORY_NAME):
        parser.add_section(_REPOSITORY_NAME)
        parser.set(_REPOSITORY_NAME, "masters", parser.defaults()["main-repo"])
    parser.set(_REPOSITORY_NAME, "location", str(overlay))
    parser.set(_REPOSITORY_NAME, "auto-sync", "no")
    stream = io.StringIO()
    parser.write(stream)
    content = stream.getvalue()
    destination.write_text(content, encoding="utf-8")
    destination.chmod(0o600)
    encoded = content.encode("utf-8")
    return content, {
        "repository": _REPOSITORY_NAME,
        "worktree": str(overlay),
        "temporary_config": str(destination),
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "complete": True,
    }


def _private_portage_config(
        baseline_root: Path, private_root: Path, atom: str, arch: str,
) -> dict:
    baseline_portage = baseline_root / "etc" / "portage"
    if not baseline_portage.is_dir():
        raise ValueError(
            f"Portage configuration directory does not exist: {baseline_portage}")
    private_portage = private_root / "etc" / "portage"
    private_portage.mkdir(mode=0o700, parents=True)
    linked_entries = 0
    for entry in sorted(baseline_portage.iterdir(), key=lambda path: path.name):
        if entry.name == "package.accept_keywords":
            continue
        (private_portage / entry.name).symlink_to(entry)
        linked_entries += 1

    baseline_keywords = baseline_portage / "package.accept_keywords"
    keywords = private_portage / "package.accept_keywords"
    keywords.mkdir(mode=0o700)
    baseline_kind = "absent"
    baseline_links: list[Path] = []
    if os.path.lexists(baseline_keywords):
        if not baseline_keywords.exists():
            raise ValueError(
                f"baseline package.accept_keywords is a broken symlink: "
                f"{baseline_keywords}")
        if baseline_keywords.is_dir():
            baseline_kind = "directory"
            for entry in sorted(baseline_keywords.iterdir(), key=lambda path: path.name):
                if (not os.path.lexists(entry) or not entry.exists()
                        or not entry.is_file()):
                    raise ValueError(
                        "baseline package.accept_keywords contains an unsupported "
                        f"entry: {entry}")
                link = keywords / entry.name
                link.symlink_to(entry)
                baseline_links.append(link)
        elif baseline_keywords.is_file():
            baseline_kind = "file"
            link = keywords / "00-system"
            link.symlink_to(baseline_keywords)
            baseline_links.append(link)
        else:
            raise ValueError(
                "baseline package.accept_keywords is not a file or directory: "
                f"{baseline_keywords}")
    target_line = f"{atom} ~{arch}\n"
    target_file = keywords / "zz-gzh-target"
    with target_file.open("x", encoding="utf-8") as stream:
        stream.write(target_line)
    target_file.chmod(0o600)
    encoded = target_line.encode("utf-8")
    return {
        "complete": True,
        "config_root": str(private_root),
        "linked_config_entries": linked_entries,
        "baseline": {
            "path": str(baseline_keywords),
            "kind": baseline_kind,
            "preserved": (
                baseline_kind == "absent" or bool(baseline_links)),
            "temporary_links": [str(path) for path in baseline_links],
        },
        "target": {
            "atom": atom,
            "keyword": f"~{arch}",
            "line": target_line.rstrip("\n"),
            "path": str(target_file),
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        },
        "cleanup": {"attempted": False, "complete": False},
        "errors": [],
    }


def _merge_commands(
        atom: str, config_root: Path,
) -> tuple[list[str], list[str]]:
    parsed = Atom(atom, allow_repo=True)
    target = parsed.cp
    common = [
        "--ignore-default-opts", f"--config-root={config_root}",
        "--verbose", "--tree", "--color=n", "--autounmask=n", "--usepkg=y",
        f"--usepkg-exclude={target}", "--oneshot", "--selective=n", atom,
    ]
    return ["emerge", "--pretend", *common], ["emerge", *common]


def _plan_change(flags: str) -> str:
    if "D" in flags:
        return "downgrade"
    if "U" in flags:
        return "upgrade"
    if "R" in flags:
        return "rebuild"
    if "N" in flags:
        return "new"
    return "other"


def _authorized_packages(values: Sequence[str]) -> list[str]:
    result = sorted(set(values))
    invalid = [value for value in result if not _CP_RE.fullmatch(value)]
    if invalid:
        raise ValueError(
            "authorized plan packages must use category/package: "
            + ", ".join(invalid))
    return result


def parse_emerge_plan(
        text: str, target_atom: str, *, authorized_packages: Sequence[str] = (),
) -> dict:
    target = Atom(target_atom, allow_repo=True)
    allowed = set(_authorized_packages(authorized_packages))
    actions = []
    total = None
    errors = []
    rejected_rows = []
    non_mutating_rows = []
    for line in text.splitlines():
        total_match = _PLAN_TOTAL_RE.match(line)
        if total_match is not None:
            if total is not None:
                errors.append("emerge pretend output contains multiple totals")
            else:
                total = int(total_match.group("count"))
            continue
        match = _PLAN_MERGE_RE.match(line)
        if match is None:
            row = _PLAN_ROW_RE.match(line)
            if row is None:
                continue
            operation = row.group("operation")
            record = {"operation": operation, "line": line.strip()}
            if operation in _NON_MUTATING_PLAN_ROWS:
                non_mutating_rows.append(record)
            else:
                rejected_rows.append(record)
                errors.append(
                    f"emerge pretend output contains unsupported action: {operation}")
            continue
        try:
            planned = Atom(f"={match.group('atom')}", allow_repo=True)
        except InvalidAtom:
            errors.append(
                f"cannot parse planned package: {match.group('atom')}")
            continue
        if planned.cpv is None or planned.repo is None:
            errors.append(
                f"planned package lacks an exact version or repository: "
                f"{match.group('atom')}")
            continue
        flags = "".join(match.group("flags").split())
        change = _plan_change(flags)
        is_target = (
            str(planned.cpv) == str(target.cpv) and planned.repo == target.repo)
        if is_target:
            category = "target"
        elif change == "new":
            category = "new_dependency"
        else:
            category = change
        actions.append({
            "atom": str(planned),
            "package": planned.cp,
            "repository": planned.repo,
            "slot": planned.slot,
            "subslot": planned.sub_slot,
            "source": match.group("source"),
            "flags": flags,
            "change": change,
            "category": category,
            "authorized": (
                is_target or category == "new_dependency" or planned.cp in allowed),
        })

    if total is None:
        errors.append("emerge pretend output has no package total")
    elif total != len(actions):
        errors.append(
            f"emerge pretend total {total} does not match {len(actions)} parsed actions")
    targets = [action for action in actions if action["category"] == "target"]
    if len(targets) != 1:
        errors.append("emerge pretend plan must contain the exact target once")
    elif targets[0]["source"] != "ebuild":
        errors.append("emerge pretend plan did not select the target ebuild")
    unauthorized = [action for action in actions if not action["authorized"]]
    classified = {
        name: [action for action in actions if action["category"] == name]
        for name in (
            "target", "new_dependency", "rebuild", "upgrade", "downgrade",
            "other")
    }
    return {
        "complete": not errors,
        "authorized": not errors and not unauthorized,
        "total": total,
        "actions": actions,
        "classified": classified,
        "authorized_packages": sorted(allowed),
        "unauthorized": unauthorized,
        "rejected_rows": rejected_rows,
        "non_mutating_rows": non_mutating_rows,
        "errors": errors,
    }


def run_verify_install(
        ebuild: Path, logdir: Path | None = None, *,
        timeout: int = DEFAULT_VERIFY_TIMEOUT,
        max_output_bytes: int = DEFAULT_VERIFY_MAX_OUTPUT_BYTES,
        environment: Mapping[str, str] | None = None,
        authorized_packages: Sequence[str] = (),
        runner: Callable[..., subprocess.CompletedProcess] | None = subprocess.run,
) -> dict:
    """Merge one exact ebuild with bounded overlay CI elog evidence."""
    if not 1 <= timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be between 1 and {MAX_TIMEOUT} seconds")
    if not 256 <= max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError(
            f"max output must be between 256 and {MAX_OUTPUT_BYTES} bytes")

    ebuild = Path(ebuild).resolve()
    atom = atom_from_ebuild(ebuild)
    overlay = validate_overlay_root(ebuild.parents[2])
    allowed_packages = _authorized_packages(authorized_packages)
    if logdir is not None:
        requested_logdir = Path(logdir).expanduser()
        if requested_logdir.is_symlink():
            raise ValueError(f"log directory must not be a symlink: {requested_logdir}")
        logdir = requested_logdir.resolve()
        logdir.mkdir(parents=True, exist_ok=True)
    else:
        logdir = Path(tempfile.mkdtemp(prefix="gzh-verify-install-"))
    elog_dir = logdir / "elog"
    try:
        elog_dir.mkdir(mode=0o700)
    except FileExistsError:
        pass
    base_environment = (
        dict(environment) if environment is not None else os.environ.copy())
    portage_environment = {
        "PORTAGE_ELOG_CLASSES": "qa warn error",
        "PORTAGE_ELOG_SYSTEM": "save",
        "PORTAGE_LOGDIR": str(logdir),
    }

    initial_elog = _elog_inventory(elog_dir, max_output_bytes)
    isolated = initial_elog["complete"] and not initial_elog["entries"]
    steps: list[dict] = []
    repository_execution = run_evidence_command(
        ["portageq", "envvar", "PORTAGE_REPOSITORIES"],
        timeout=min(timeout, 30), max_output_bytes=max_output_bytes,
        env=base_environment, runner=runner)
    baseline_arch_execution = run_evidence_command(
        ["portageq", "envvar", "ARCH"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, env=base_environment, runner=runner)
    baseline_root_execution = run_evidence_command(
        ["portageq", "envvar", "ROOT"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, env=base_environment, runner=runner)
    config_root_execution = run_evidence_command(
        ["portageq", "envvar", "PORTAGE_CONFIGROOT"],
        timeout=min(timeout, 30), max_output_bytes=max_output_bytes,
        env=base_environment, runner=runner)
    baseline_keywords_execution = run_evidence_command(
        ["portageq", "envvar", "ACCEPT_KEYWORDS"], timeout=min(timeout, 30),
        max_output_bytes=max_output_bytes, env=base_environment, runner=runner)
    baseline_arch = _environment_value(
        baseline_arch_execution, pattern=_ARCH_RE)
    baseline_root = _absolute_path_value(baseline_root_execution)
    baseline_config_root = _absolute_path_value(config_root_execution)
    baseline_keywords = _environment_value(
        baseline_keywords_execution, pattern=_KEYWORDS_RE)
    repository_binding = {
        "repository": _REPOSITORY_NAME,
        "worktree": str(overlay),
        "temporary_config": None,
        "bytes": None,
        "sha256": None,
        "configuration_complete": False,
        "complete": False,
        "execution": repository_execution,
        "verification": {
            "path": None,
            "matches_worktree": False,
            "execution": {
                "command": [
                    "portageq", "get_repo_path",
                    str(baseline_root) if baseline_root is not None else "",
                    _REPOSITORY_NAME],
                "complete": False,
                "truncated": False,
                "returncode": None,
                "stdout": "",
            },
        },
        "errors": [],
    }
    keyword_configuration = {
        "complete": False,
        "config_root": None,
        "linked_config_entries": 0,
        "baseline": {
            "path": None,
            "kind": None,
            "preserved": False,
            "temporary_links": [],
        },
        "target": {
            "atom": atom,
            "keyword": f"~{baseline_arch}" if baseline_arch is not None else None,
            "line": None,
            "path": None,
            "bytes": None,
            "sha256": None,
        },
        "cleanup": {"attempted": False, "path": None, "complete": False},
        "errors": [],
    }
    emerge_version = {
        "command": ["emerge", "--version"], "version": None,
        "complete": False, "execution": {
            "command": ["emerge", "--version"], "complete": False,
            "truncated": False, "returncode": None,
        },
    }
    profile_execution = {
        "command": ["eselect", "--brief", "profile", "show"],
        "complete": False, "truncated": False, "returncode": None,
        "stdout": "",
    }
    arch_execution = {
        "command": ["portageq", "envvar", "ARCH"],
        "complete": False, "truncated": False, "returncode": None,
        "stdout": "",
    }
    root_execution = {
        "command": ["portageq", "envvar", "ROOT"],
        "complete": False, "truncated": False, "returncode": None,
        "stdout": "",
    }
    keywords_execution = {
        "command": ["portageq", "envvar", "ACCEPT_KEYWORDS"],
        "complete": False, "truncated": False, "returncode": None,
        "stdout": "",
    }
    repository_path_execution = repository_binding["verification"]["execution"]
    profile = None
    arch = None
    root = None
    keywords = None
    environment_complete = False
    target_elog = _pending_elog_inventory(elog_dir)
    plan = {
        "complete": False,
        "authorized": False,
        "total": None,
        "actions": [],
        "classified": {
            name: [] for name in (
                "target", "new_dependency", "rebuild", "upgrade",
                "downgrade", "other")
        },
        "authorized_packages": allowed_packages,
        "unauthorized": [],
        "rejected_rows": [],
        "non_mutating_rows": [],
        "errors": [],
    }
    failed_step: str | None = None
    errors: list[str] = []
    env = dict(base_environment)
    env.update(portage_environment)
    temporary_root: Path | None = None
    with tempfile.TemporaryDirectory(
            prefix="gzh-verify-install-config-") as temporary:
        temporary_root = Path(temporary)
        config_path = temporary_root / "repos.conf"
        private_config_root = temporary_root / "config-root"
        keyword_configuration["config_root"] = str(private_config_root)
        keyword_configuration["cleanup"]["path"] = str(private_config_root)
        if (repository_execution.get("complete") is not True
                or repository_execution.get("truncated") is True
                or repository_execution.get("returncode") != 0):
            failed_step = "preflight"
            errors.append("Portage repository configuration evidence is incomplete")
        else:
            try:
                config_text, binding = _owned_repositories_config(
                    repository_execution["stdout"], overlay, config_path)
            except (OSError, ValueError) as exc:
                failed_step = "preflight"
                repository_binding["errors"].append(str(exc))
                errors.append("cannot bind Portage to the selected worktree")
            else:
                repository_binding.update(binding)
                repository_binding["configuration_complete"] = True
                repository_binding["complete"] = False
                env["PORTAGE_REPOSITORIES"] = config_text

        if baseline_arch is None:
            failed_step = "preflight"
            errors.append("baseline ARCH evidence is incomplete")
        if baseline_root is None:
            failed_step = "preflight"
            errors.append("baseline ROOT evidence is incomplete")
        if baseline_config_root is None:
            failed_step = "preflight"
            errors.append("Portage configuration root evidence is incomplete")
        if baseline_keywords is None:
            failed_step = "preflight"
            errors.append("baseline ACCEPT_KEYWORDS evidence is incomplete")

        if (repository_binding["configuration_complete"]
                and baseline_arch is not None
                and baseline_root is not None
                and baseline_config_root is not None
                and baseline_keywords is not None):
            try:
                keyword_configuration = _private_portage_config(
                    baseline_config_root, private_config_root, atom, baseline_arch)
            except (OSError, ValueError) as exc:
                failed_step = "preflight"
                keyword_configuration["errors"].append(str(exc))
                errors.append("cannot create a private Portage configuration")
            else:
                env["PORTAGE_CONFIGROOT"] = str(private_config_root)

        if failed_step is None:
            emerge_version = read_tool_version(
                ["emerge", "--version"], timeout=min(timeout, 30),
                max_output_bytes=min(max_output_bytes, 4096), env=env, runner=runner)
            profile_execution = run_evidence_command(
                ["eselect", "--brief", "profile", "show"],
                timeout=min(timeout, 30), max_output_bytes=max_output_bytes,
                env=env, runner=runner)
            arch_execution = run_evidence_command(
                ["portageq", "envvar", "ARCH"], timeout=min(timeout, 30),
                max_output_bytes=max_output_bytes, env=env, runner=runner)
            root_execution = run_evidence_command(
                ["portageq", "envvar", "ROOT"], timeout=min(timeout, 30),
                max_output_bytes=max_output_bytes, env=env, runner=runner)
            keywords_execution = run_evidence_command(
                ["portageq", "envvar", "ACCEPT_KEYWORDS"],
                timeout=min(timeout, 30), max_output_bytes=max_output_bytes,
                env=env, runner=runner)
            profile = _environment_value(profile_execution, pattern=_PROFILE_RE)
            arch = _environment_value(arch_execution, pattern=_ARCH_RE)
            root = _absolute_path_value(root_execution)
            keywords = _environment_value(
                keywords_execution, pattern=_KEYWORDS_RE)
            repository_path_execution = run_evidence_command(
                ["portageq", "get_repo_path",
                 str(root) if root is not None else str(baseline_root),
                 _REPOSITORY_NAME],
                timeout=min(timeout, 30), max_output_bytes=max_output_bytes,
                env=env, runner=runner)
            repository_path = _absolute_path_value(repository_path_execution)
            repository_matches = repository_path == overlay
            repository_binding["verification"] = {
                "path": str(repository_path) if repository_path is not None else None,
                "matches_worktree": repository_matches,
                "execution": repository_path_execution,
            }
            repository_binding["complete"] = (
                repository_binding["configuration_complete"]
                and repository_matches)
            environment_complete = (
                repository_binding["complete"] and keyword_configuration["complete"]
                and emerge_version["complete"] and profile is not None
                and arch is not None and arch == baseline_arch
                and root is not None and root == baseline_root
                and keywords is not None and keywords == baseline_keywords)
            if not emerge_version["complete"]:
                errors.append("emerge version evidence is incomplete")
            if profile is None:
                errors.append("active profile evidence is incomplete")
            if arch is None:
                errors.append("ARCH evidence is incomplete")
            elif arch != baseline_arch:
                errors.append("private Portage configuration changed ARCH")
            if root is None:
                errors.append("ROOT evidence is incomplete")
            elif root != baseline_root:
                errors.append("private Portage configuration changed ROOT")
            if keywords is None:
                errors.append("ACCEPT_KEYWORDS evidence is incomplete")
            elif keywords != baseline_keywords:
                errors.append(
                    "private Portage configuration changed global ACCEPT_KEYWORDS")
            if repository_path is None:
                errors.append("repository path evidence is incomplete")
            elif not repository_matches:
                repository_binding["errors"].append(
                    "private Portage configuration did not select the worktree")
                errors.append("Portage did not select the requested worktree")
            if not isolated:
                errors.append(
                    "isolated elog directory is not empty or could not be inspected")
            if not environment_complete or not isolated:
                failed_step = "preflight"

        if failed_step is None:
            pretend_command, merge_command = _merge_commands(
                atom, private_config_root)
            pretend = _run_emerge(
                pretend_command, env=env, timeout=timeout,
                max_output_bytes=max_output_bytes, runner=runner)
            steps.append(_step("pretend", pretend))
            if pretend["complete"] is not True or pretend["truncated"] is True:
                failed_step = "pretend"
                errors.append("emerge pretend produced incomplete evidence")
            elif pretend["returncode"] != 0:
                failed_step = "pretend"
                errors.append("emerge pretend failed")
            else:
                plan = parse_emerge_plan(
                    pretend["stdout"], atom,
                    authorized_packages=allowed_packages)
                if not plan["complete"]:
                    failed_step = "plan"
                    errors.extend(plan["errors"])
                elif not plan["authorized"]:
                    failed_step = "plan-authorization"
                    errors.append("emerge pretend contains unauthorized package actions")
            if failed_step is None:
                merge = _run_emerge(
                    merge_command, env=env, timeout=timeout,
                    max_output_bytes=max_output_bytes, runner=runner)
                steps.append(_step("merge", merge))
                target_elog = _elog_inventory(elog_dir, max_output_bytes)
                if merge["complete"] is not True or merge["truncated"] is True:
                    failed_step = "merge"
                    errors.append("target merge produced incomplete evidence")
                elif merge["returncode"] != 0:
                    failed_step = "merge"
                    errors.append("target merge failed")
                elif any(
                        entry["kind"] == "file"
                        for entry in target_elog["entries"]):
                    failed_step = "elog"
                    errors.append("target or dependency merge produced a saved elog entry")
                elif not target_elog["complete"]:
                    failed_step = "evidence"
                    errors.append("target elog inventory is incomplete")

    assert temporary_root is not None
    keyword_configuration["cleanup"] = {
        "attempted": True,
        "path": str(temporary_root),
        "complete": not os.path.lexists(temporary_root),
    }
    if not keyword_configuration["cleanup"]["complete"]:
        failed_step = failed_step or "cleanup"
        errors.append("temporary Portage configuration cleanup is incomplete")

    executions = [
        repository_execution, baseline_arch_execution, config_root_execution,
        baseline_root_execution, baseline_keywords_execution,
        emerge_version["execution"], profile_execution, arch_execution,
        root_execution, keywords_execution, repository_path_execution,
        *(dict(step) for step in steps),
    ]
    truncated = (
        initial_elog["truncated"] or target_elog["truncated"]
        or any(item.get("truncated") is True for item in executions))
    timed_out = any(item.get("timed_out") is True for item in executions)
    execution_complete = all(
        item.get("complete") is True and item.get("truncated") is not True
        for item in executions)
    inventory_complete = initial_elog["complete"]
    if target_elog["state"] != "not-collected":
        inventory_complete = inventory_complete and target_elog["complete"]
    pretend_steps = [step for step in steps if step["name"] == "pretend"]
    if pretend_steps:
        pretend = pretend_steps[0]
        plan_evidence_complete = (
            plan["complete"]
            or (pretend.get("complete") is True
                and pretend.get("truncated") is not True
                and pretend.get("returncode") != 0))
    else:
        plan_evidence_complete = (
            failed_step == "preflight" and environment_complete
            and initial_elog["complete"] and not isolated)
    complete = (
        environment_complete and execution_complete and inventory_complete
        and plan_evidence_complete
        and keyword_configuration["cleanup"]["complete"])
    ok = complete and failed_step is None

    if not environment_complete:
        state = "environment-incomplete"
    elif not isolated:
        state = "preflight-failed"
    elif timed_out:
        state = "timed-out"
    elif truncated:
        state = "truncated"
    elif failed_step is not None:
        state = "failed"
    elif complete:
        state = "passed"
    else:
        state = "incomplete"

    elog_files = [
        {"step": step_name, "path": entry["path"], "text": entry["text"],
         "size": entry["size"], "sha256": entry["sha256"],
         "truncated": entry["truncated"]}
        for step_name, inventory in (("merge", target_elog),)
        for entry in inventory["entries"] if entry["kind"] == "file"
    ]
    commands = [
        repository_execution["command"],
        baseline_arch_execution["command"], baseline_root_execution["command"],
        config_root_execution["command"],
        baseline_keywords_execution["command"],
        emerge_version["execution"]["command"],
        profile_execution["command"], arch_execution["command"],
        root_execution["command"],
        keywords_execution["command"],
        repository_path_execution["command"],
        *(step["command"] for step in steps),
    ]
    return {
        "schema_version": 1,
        "operation": "verify-install",
        "side_effectful": True,
        "atom": atom,
        "logdir": str(logdir),
        "options": {
            "source_only_target": True,
            "source_only_dependencies": False,
            "authorized_packages": allowed_packages,
            "timeout_seconds": timeout,
            "max_output_bytes": max_output_bytes,
        },
        "tool": {"emerge": emerge_version},
        "repository_binding": repository_binding,
        "keyword_configuration": keyword_configuration,
        "environment": {
            "baseline_arch": {
                "value": baseline_arch, "execution": baseline_arch_execution},
            "root": {
                "baseline": (
                    str(baseline_root) if baseline_root is not None else None),
                "value": str(root) if root is not None else None,
                "baseline_execution": baseline_root_execution,
                "execution": root_execution,
            },
            "config_root": {
                "value": (
                    str(baseline_config_root)
                    if baseline_config_root is not None else None),
                "execution": config_root_execution,
            },
            "accept_keywords": {
                "baseline": baseline_keywords,
                "value": keywords,
                "baseline_execution": baseline_keywords_execution,
                "execution": keywords_execution,
            },
            "profile": {"value": profile, "execution": profile_execution},
            "arch": {"value": arch, "execution": arch_execution},
            "portage": {
                **portage_environment,
                "PORTAGE_CONFIGROOT": keyword_configuration["config_root"],
            },
        },
        "commands": commands,
        "steps": steps,
        "plan": plan,
        "initial_elog": initial_elog,
        "elog": target_elog,
        "elog_files": elog_files,
        "ok": ok,
        "complete": complete,
        "timed_out": timed_out,
        "truncated": truncated,
        "state": state,
        "failed_step": failed_step,
        "errors": errors,
    }
