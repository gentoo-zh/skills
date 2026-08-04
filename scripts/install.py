#!/usr/bin/env python3
"""Install the gentoo-zh skills and gzh CLI for supported agent clients."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = ROOT / ".agents" / "skills"
SKILL_NAMES = tuple(sorted(
    path.name for path in SKILLS_ROOT.iterdir()
    if path.is_dir() and (path / "SKILL.md").is_file()))
INSTALLER_ID = "gentoo-zh/skills"
SKILL_MARKER = ".gzh-skill-install.json"
GZH_MARKER = ".gzh-install.json"
INSTALLATION_STATE = "skill-installations.json"
CLIENT_NAMES = {"claude", "codex", "opencode"}
IGNORED_NAMES = {".git", ".hg", ".svn", "__pycache__", SKILL_MARKER}


class InstallError(RuntimeError):
    pass


def expand_env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default.expanduser()


def codex_destination(home: Path | None = None) -> Path:
    home = home or Path.home()
    configured = os.environ.get("CODEX_HOME")
    return ((Path(configured).expanduser() / "skills") if configured
            else home / ".agents" / "skills")


def destinations(clients: list[str]) -> dict[Path, list[str]]:
    home = Path.home()
    selected = set(clients)
    result: dict[Path, list[str]] = {}
    if "codex" in selected:
        base = codex_destination(home)
        result.setdefault(base, []).append("codex")
    if "claude" in selected:
        base = expand_env_path("CLAUDE_CONFIG_DIR", home / ".claude") / "skills"
        result.setdefault(base, []).append("claude")
    if "opencode" in selected:
        discovered = set(opencode_discovery_destinations())
        codex_base = codex_destination(home)
        claude_base = expand_env_path(
            "CLAUDE_CONFIG_DIR", home / ".claude") / "skills"
        if "codex" in selected and codex_base in discovered:
            base = codex_base
        elif "claude" in selected and claude_base in discovered:
            base = claude_base
        else:
            base = expand_env_path(
                "XDG_CONFIG_HOME", home / ".config") / "opencode" / "skills"
        result.setdefault(base, []).append("opencode")
    return result


def all_known_destinations() -> list[Path]:
    home = Path.home()
    paths = [
        codex_destination(home),
        home / ".agents" / "skills",
        home / ".codex" / "skills",
        expand_env_path("CLAUDE_CONFIG_DIR", home / ".claude") / "skills",
        expand_env_path(
            "XDG_CONFIG_HOME", home / ".config") / "opencode" / "skills",
    ]
    return list(dict.fromkeys(paths))


def codex_discovery_destinations() -> list[Path]:
    home = Path.home()
    paths = [codex_destination(home), home / ".agents" / "skills",
             home / ".codex" / "skills"]
    return list(dict.fromkeys(paths))


def opencode_discovery_destinations() -> list[Path]:
    home = Path.home()
    paths = [expand_env_path(
        "XDG_CONFIG_HOME", home / ".config") / "opencode" / "skills"]
    if os.environ.get("OPENCODE_DISABLE_EXTERNAL_SKILLS") != "1":
        if os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS") != "1":
            paths.append(
                expand_env_path("CLAUDE_CONFIG_DIR", home / ".claude") / "skills")
        paths.append(home / ".agents" / "skills")
    return list(dict.fromkeys(paths))


def opencode_duplicate_bases(
        clients: list[str], records: dict[Path, dict],
        planned: set[Path] | None = None) -> list[Path]:
    active = "opencode" in clients or any(
        "opencode" in record["clients"] for record in records.values())
    if not active:
        return []
    planned = planned or set()
    duplicates = []
    for candidate in opencode_discovery_destinations():
        base = absolute_path(candidate)
        if base in planned or any(path_present(base / name) for name in SKILL_NAMES):
            duplicates.append(base)
    return sorted(set(duplicates), key=str)


def gzh_paths() -> tuple[Path, Path]:
    home = Path.home()
    data_home = expand_env_path("XDG_DATA_HOME", home / ".local" / "share")
    install_root = expand_env_path(
        "GZH_INSTALL_ROOT", data_home / "gentoo-zh-skills" / "gzh")
    bin_dir = expand_env_path("GZH_BIN_DIR", home / ".local" / "bin")
    return install_root, bin_dir / "gzh"


def directory_on_path(directory: Path) -> bool:
    target = absolute_path(directory)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(entry) if entry else Path.cwd()
        if absolute_path(candidate) == target:
            return True
    return False


def warn_if_gzh_not_on_path(executable: Path) -> None:
    if not directory_on_path(executable.parent):
        print(
            f"warning: {executable.parent} is not in PATH; "
            "add it before invoking gzh",
            file=sys.stderr,
        )


def absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path.expanduser()))


def installation_state_path() -> Path:
    home = Path.home()
    data_home = expand_env_path("XDG_DATA_HOME", home / ".local" / "share")
    return data_home / "gentoo-zh-skills" / INSTALLATION_STATE


def installation_lock_path() -> Path:
    state = installation_state_path()
    return state.parent / f".{state.name}.lock"


@contextmanager
def installation_mutation_lock() -> Iterator[None]:
    path = installation_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise InstallError(f"cannot open managed installation lock: {path}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InstallError(f"invalid managed installation lock: {path}")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def valid_skill_name(name: object) -> bool:
    return (isinstance(name, str) and bool(name)
            and Path(name).name == name and name not in {".", ".."})


def skill_bundle_record(base: Path, clients: list[str], mode: str,
                        skills: tuple[str, ...] | list[str],
                        source: Path = SKILLS_ROOT) -> dict:
    return {
        "target": str(absolute_path(base)),
        "clients": sorted(set(clients)),
        "mode": mode,
        "source": str(absolute_path(source)),
        "skills": sorted(set(skills)),
    }


def read_installation_state() -> dict[Path, dict]:
    path = installation_state_path()
    if not path.exists() and not path.is_symlink():
        return {}
    if path.is_symlink() or not path.is_file():
        raise InstallError(f"invalid managed installation state: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"invalid managed installation state: {path}") from exc
    if (not isinstance(data, dict) or data.get("schema") != 1
            or data.get("installer") != INSTALLER_ID
            or not isinstance(data.get("targets"), list)):
        raise InstallError(f"invalid managed installation state: {path}")
    records: dict[Path, dict] = {}
    for record in data["targets"]:
        if not isinstance(record, dict):
            raise InstallError(f"invalid managed installation state: {path}")
        target_value = record.get("target")
        source_value = record.get("source")
        clients = record.get("clients")
        skills = record.get("skills")
        mode = record.get("mode")
        if (not isinstance(target_value, str) or not Path(target_value).is_absolute()
                or not isinstance(source_value, str) or not Path(source_value).is_absolute()
                or not isinstance(clients, list) or not clients
                or any(client not in CLIENT_NAMES for client in clients)
                or len(clients) != len(set(clients))
                or mode not in {"copy", "link"}
                or not isinstance(skills, list)
                or any(not valid_skill_name(name) for name in skills)
                or len(skills) != len(set(skills))):
            raise InstallError(f"invalid managed installation state: {path}")
        target = absolute_path(Path(target_value))
        if target in records:
            raise InstallError(f"invalid managed installation state: {path}")
        records[target] = skill_bundle_record(
            target, clients, mode, skills, Path(source_value))
    return records


def write_installation_state(records: dict[Path, dict]) -> None:
    path = installation_state_path()
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise InstallError(f"refusing to replace unowned path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "installer": INSTALLER_ID,
        "targets": [records[target] for target in sorted(records, key=str)],
    }
    descriptor, staged_name = tempfile.mkstemp(
        prefix=f".{path.name}.new.", dir=path.parent)
    staged = Path(staged_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
        os.replace(staged, path)
    except Exception:
        if staged.exists():
            staged.unlink()
        raise


def read_marker(path: Path, marker_name: str) -> dict | None:
    marker = path / marker_name
    if not marker.is_file():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if data.get("installer") == INSTALLER_ID else None


def owned_skill(destination: Path, source: Path) -> tuple[bool, str | None]:
    if destination.is_symlink():
        try:
            return destination.resolve(strict=False) == source.resolve(), "link"
        except OSError:
            return False, None
    marker = read_marker(destination, SKILL_MARKER)
    if marker and marker.get("skill") == source.name:
        return True, marker.get("mode")
    return False, None


def path_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def recorded_skill_owned(record: dict, name: str) -> bool:
    destination = Path(record["target"]) / name
    if record["mode"] == "link":
        if not destination.is_symlink():
            return False
        try:
            expected = Path(record["source"]) / name
            return destination.resolve(strict=False) == expected.resolve(strict=False)
        except OSError:
            return False
    marker = read_marker(destination, SKILL_MARKER)
    return bool(marker and marker.get("skill") == name
                and marker.get("mode") == "copy")


def inferred_clients(base: Path) -> list[str]:
    base = absolute_path(base)
    clients = []
    if base in map(absolute_path, codex_discovery_destinations()):
        clients.append("codex")
    claude = expand_env_path(
        "CLAUDE_CONFIG_DIR", Path.home() / ".claude") / "skills"
    if base == absolute_path(claude):
        clients.append("claude")
    if base in map(absolute_path, opencode_discovery_destinations()):
        clients.append("opencode")
    return clients


def legacy_skill_bundle(base: Path, clients: list[str] | None = None) -> dict | None:
    base = absolute_path(base)
    managed = []
    modes = set()
    for name in SKILL_NAMES:
        destination = base / name
        if not path_present(destination):
            continue
        owned, mode = owned_skill(destination, SKILLS_ROOT / name)
        if owned:
            managed.append(name)
            modes.add(mode)
    if not managed:
        return None
    if len(modes) != 1:
        raise InstallError(f"managed skills use mixed modes at {base}")
    detected_clients = clients or inferred_clients(base)
    if not detected_clients:
        raise InstallError(f"cannot identify clients for managed skills at {base}")
    return skill_bundle_record(
        base, detected_clients, modes.pop(), managed, SKILLS_ROOT)


def inventory(root: Path) -> dict[str, tuple]:
    entries: dict[str, tuple] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if set(relative.parts).intersection(IGNORED_NAMES):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        key = str(relative)
        if path.is_symlink():
            entries[key] = ("link", os.readlink(path))
        elif path.is_file():
            entries[key] = (
                "file", path.stat().st_mode & 0o777,
                hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            entries[key] = ("directory",)
    return entries


def copy_current(source: Path, destination: Path) -> bool:
    return inventory(source) == inventory(destination)


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def swap_path(staged: Path, destination: Path) -> Path | None:
    backup = None
    if destination.exists() or destination.is_symlink():
        backup = Path(tempfile.mkdtemp(
            prefix=f".{destination.name}.old.", dir=destination.parent))
        backup.rmdir()
        destination.rename(backup)
    try:
        staged.rename(destination)
    except Exception:
        if backup is not None:
            backup.rename(destination)
        raise
    return backup


def finish_swap(backup: Path | None) -> None:
    if backup is not None:
        remove_path(backup)


def rollback_swap(destination: Path, backup: Path | None) -> None:
    remove_path(destination)
    if backup is not None:
        backup.rename(destination)


def validate_sources() -> None:
    for name in SKILL_NAMES:
        source = SKILLS_ROOT / name
        text = (source / "SKILL.md").read_text(encoding="utf-8")
        if not text.startswith("---\n") or f"name: {name}\n" not in text.split("---", 2)[1]:
            raise InstallError(f"invalid skill source: {source}")


def stage_skill(source: Path, base: Path, mode: str) -> Path:
    base.mkdir(parents=True, exist_ok=True)
    stage_parent = Path(tempfile.mkdtemp(prefix=f".{source.name}.new.", dir=base))
    staged = stage_parent / source.name
    if mode == "link":
        staged.symlink_to(source)
    else:
        shutil.copytree(
            source, staged, symlinks=True,
            ignore=shutil.ignore_patterns(
                ".git", ".hg", ".svn", "__pycache__", "*.pyc", "*.pyo"))
        marker = {
            "schema": 1,
            "installer": INSTALLER_ID,
            "skill": source.name,
            "mode": "copy",
        }
        (staged / SKILL_MARKER).write_text(
            json.dumps(marker, indent=2) + "\n", encoding="utf-8")
    return staged


def install_skill(source: Path, destination: Path, mode: str) -> str:
    exists = destination.exists() or destination.is_symlink()
    if exists and not owned_skill(destination, source)[0]:
        raise InstallError(f"refusing to replace unowned path: {destination}")
    staged = stage_skill(source, destination.parent, mode)
    stage_parent = staged.parent
    backup = None
    try:
        backup = swap_path(staged, destination)
    finally:
        if stage_parent.exists():
            shutil.rmtree(stage_parent, ignore_errors=True)
    finish_swap(backup)
    return f"{mode}, current"


def validate_skill_bundle(old_record: dict | None, new_record: dict) -> None:
    base = Path(new_record["target"])
    old_skills = set(old_record["skills"]) if old_record else set()
    if old_record and old_record["target"] != new_record["target"]:
        raise InstallError("managed skill target changed during an installation transaction")
    for name in old_skills:
        destination = base / name
        if path_present(destination) and not recorded_skill_owned(old_record, name):
            raise InstallError(f"refusing to replace unowned path: {destination}")
    for name in new_record["skills"]:
        destination = base / name
        if name not in old_skills and path_present(destination):
            raise InstallError(f"refusing to replace unowned path: {destination}")


def synchronize_skill_bundle(records: dict[Path, dict], old_record: dict | None,
                             new_record: dict) -> None:
    validate_skill_bundle(old_record, new_record)
    base = Path(new_record["target"])
    base_existed = base.exists()
    old_skills = set(old_record["skills"]) if old_record else set()
    new_skills = set(new_record["skills"])
    staged_skills: dict[str, Path] = {}
    swapped: list[tuple[Path, Path | None]] = []
    retired: list[tuple[Path, Path]] = []
    try:
        for name in sorted(new_skills):
            staged_skills[name] = stage_skill(
                Path(new_record["source"]) / name, base, new_record["mode"])
        try:
            for name in sorted(new_skills):
                destination = base / name
                backup = swap_path(staged_skills[name], destination)
                swapped.append((destination, backup))
            for name in sorted(old_skills - new_skills):
                destination = base / name
                if not path_present(destination):
                    continue
                backup = Path(tempfile.mkdtemp(
                    prefix=f".{name}.old.", dir=base))
                backup.rmdir()
                destination.rename(backup)
                retired.append((destination, backup))
            updated = dict(records)
            updated[base] = new_record
            write_installation_state(updated)
        except Exception:
            for destination, backup in reversed(retired):
                if path_present(destination):
                    remove_path(destination)
                backup.rename(destination)
            for destination, backup in reversed(swapped):
                rollback_swap(destination, backup)
            raise
        else:
            records.clear()
            records.update(updated)
            for _destination, backup in swapped:
                finish_swap(backup)
            for _destination, backup in retired:
                finish_swap(backup)
    finally:
        for staged in staged_skills.values():
            if staged.parent.exists():
                shutil.rmtree(staged.parent, ignore_errors=True)
        if not base_existed and base.exists():
            try:
                base.rmdir()
            except OSError:
                pass


def validate_remove_skill_bundle(record: dict) -> None:
    base = Path(record["target"])
    for name in record["skills"]:
        destination = base / name
        if path_present(destination) and not recorded_skill_owned(record, name):
            raise InstallError(f"refusing to remove unowned path: {destination}")


def remove_skill_bundle(records: dict[Path, dict], record: dict) -> None:
    validate_remove_skill_bundle(record)
    base = Path(record["target"])
    removed: list[tuple[Path, Path]] = []
    try:
        for name in record["skills"]:
            destination = base / name
            if not path_present(destination):
                continue
            backup = Path(tempfile.mkdtemp(
                prefix=f".{name}.old.", dir=base))
            backup.rmdir()
            destination.rename(backup)
            removed.append((destination, backup))
        updated = dict(records)
        updated.pop(base, None)
        write_installation_state(updated)
    except Exception:
        for destination, backup in reversed(removed):
            if path_present(destination):
                remove_path(destination)
            backup.rename(destination)
        raise
    else:
        records.clear()
        records.update(updated)
        for _destination, backup in removed:
            finish_swap(backup)


def skill_status(source: Path, destination: Path) -> tuple[str, bool]:
    if not destination.exists() and not destination.is_symlink():
        return "not installed", False
    owned, mode = owned_skill(destination, source)
    if not owned:
        return "unowned", False
    if mode == "link":
        return "link, current", True
    current = copy_current(source, destination)
    return f"copy, {'current' if current else 'stale'}", current


def recorded_skill_status(record: dict, name: str) -> tuple[str, bool]:
    destination = Path(record["target"]) / name
    if not path_present(destination):
        return "not installed", False
    if not recorded_skill_owned(record, name):
        return "unowned", False
    if name not in SKILL_NAMES:
        return f"{record['mode']}, retired", False
    if record["mode"] == "link":
        current = destination.resolve(strict=False) == (SKILLS_ROOT / name).resolve()
    else:
        current = copy_current(SKILLS_ROOT / name, destination)
    return f"{record['mode']}, {'current' if current else 'stale'}", current


def executable_owned(executable: Path, install_root: Path) -> bool:
    expected = install_root / "venv" / "bin" / "gzh"
    return executable.is_symlink() and executable.resolve(strict=False) == expected


def gzh_owned(install_root: Path) -> bool:
    marker = read_marker(install_root, GZH_MARKER)
    return bool(marker and marker.get("component") == "gzh")


def rewrite_venv_prefix(staged: Path, install_root: Path) -> None:
    old = str(staged / "venv").encode()
    new = str(install_root / "venv").encode()
    for path in (staged / "venv" / "bin").iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        content = path.read_bytes()
        if old in content:
            path.write_bytes(content.replace(old, new))


def stage_gzh(install_root: Path) -> Path:
    install_root.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".gzh.new.", dir=install_root.parent))
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", "--system-site-packages",
             str(staged / "venv")], check=True)
        subprocess.run(
            [str(staged / "venv" / "bin" / "python"), "-m", "pip", "install",
             "--disable-pip-version-check",
             str(ROOT / "gzh")], check=True)
        rewrite_venv_prefix(staged, install_root)
        marker = {
            "schema": 1,
            "installer": INSTALLER_ID,
            "component": "gzh",
        }
        (staged / GZH_MARKER).write_text(
            json.dumps(marker, indent=2) + "\n", encoding="utf-8")
        return staged
    except Exception:
        shutil.rmtree(staged, ignore_errors=True)
        raise


def install_gzh() -> str:
    install_root, executable = gzh_paths()
    validate_gzh_destination(install_root, executable)

    staged_root = stage_gzh(install_root)
    executable.parent.mkdir(parents=True, exist_ok=True)
    staged_link_dir = Path(tempfile.mkdtemp(
        prefix=".gzh-bin.new.", dir=executable.parent))
    staged_link = staged_link_dir / "gzh"
    staged_link.symlink_to(install_root / "venv" / "bin" / "gzh")
    root_backup = bin_backup = None
    root_swapped = bin_swapped = False
    try:
        root_backup = swap_path(staged_root, install_root)
        root_swapped = True
        bin_backup = swap_path(staged_link, executable)
        bin_swapped = True
    except Exception:
        if bin_swapped:
            rollback_swap(executable, bin_backup)
        if root_swapped:
            rollback_swap(install_root, root_backup)
        raise
    else:
        finish_swap(root_backup)
        finish_swap(bin_backup)
    finally:
        if staged_root.exists():
            shutil.rmtree(staged_root, ignore_errors=True)
        if staged_link_dir.exists():
            shutil.rmtree(staged_link_dir, ignore_errors=True)
    return str(executable)


def validate_gzh_destination(install_root: Path, executable: Path) -> None:
    if (install_root.exists() or install_root.is_symlink()
            ) and not gzh_owned(install_root):
        raise InstallError(f"refusing to replace unowned path: {install_root}")
    if (executable.exists() or executable.is_symlink()
            ) and not executable_owned(executable, install_root):
        raise InstallError(f"refusing to replace unowned path: {executable}")


def gzh_status() -> tuple[str, bool]:
    install_root, executable = gzh_paths()
    if not install_root.exists() and not executable.exists() and not executable.is_symlink():
        return "not installed", False
    if not gzh_owned(install_root):
        return f"unowned ({install_root})", False
    if not executable_owned(executable, install_root):
        return f"managed environment; launcher missing or unowned ({executable})", False
    proc = subprocess.run(
        [str(executable), "--help"], capture_output=True, text=True)
    if proc.returncode != 0:
        return "installed; launcher failed", False
    return f"installed ({executable})", True


def uninstall_gzh() -> str:
    install_root, executable = gzh_paths()
    if not install_root.exists() and not executable.exists() and not executable.is_symlink():
        return "not installed"
    if install_root.exists() and not gzh_owned(install_root):
        raise InstallError(f"refusing to remove unowned path: {install_root}")
    if (executable.exists() or executable.is_symlink()
            ) and not executable_owned(executable, install_root):
        raise InstallError(f"refusing to remove unowned path: {executable}")
    if executable.exists() or executable.is_symlink():
        executable.unlink()
    if install_root.exists():
        shutil.rmtree(install_root)
    return "removed"


def run_install(clients: list[str], mode: str, include_skills: bool,
                include_gzh: bool) -> int:
    validate_sources()
    targets = {
        absolute_path(base): consumers
        for base, consumers in destinations(clients).items()
    }
    records = read_installation_state() if include_skills else {}
    bundles: dict[Path, tuple[dict | None, dict]] = {}
    conflicts = []
    if include_skills:
        for base, consumers in targets.items():
            try:
                old_record = records.get(base) or legacy_skill_bundle(base, consumers)
                recorded_clients = old_record["clients"] if old_record else []
                new_record = skill_bundle_record(
                    base, recorded_clients + consumers, mode, SKILL_NAMES)
                validate_skill_bundle(old_record, new_record)
                bundles[base] = old_record, new_record
            except InstallError as exc:
                conflicts.append(str(exc))
        duplicate_bases = opencode_duplicate_bases(
            clients, records, planned=set(targets))
        if len(duplicate_bases) > 1:
            paths = ", ".join(str(path) for path in duplicate_bases)
            conflicts.append(f"installation would create duplicate OpenCode skills: {paths}")
        if "codex" in clients:
            planned_bases = set(targets)
            for base in codex_discovery_destinations():
                base = absolute_path(base)
                if base in planned_bases:
                    continue
                for name in SKILL_NAMES:
                    alternate = base / name
                    if path_present(alternate):
                        conflicts.append(
                            f"duplicate Codex skill exists outside the target: {alternate}")
    if include_gzh:
        try:
            validate_gzh_destination(*gzh_paths())
        except InstallError as exc:
            conflicts.append(str(exc))
    if conflicts:
        for conflict in conflicts:
            print(f"error: {conflict}", file=sys.stderr)
        return 1

    failed = False
    if include_skills:
        for base, consumers in targets.items():
            label = "+".join(consumers)
            old_record, new_record = bundles[base]
            try:
                synchronize_skill_bundle(records, old_record, new_record)
                for name in SKILL_NAMES:
                    destination = base / name
                    print(f"{label:<18} {name:<23} {mode}, current ({destination})")
                if old_record:
                    for name in sorted(set(old_record["skills"]) - set(SKILL_NAMES)):
                        print(f"{label:<18} {name:<23} removed")
            except (InstallError, OSError) as exc:
                print(f"{label:<18} {'bundle':<23} {exc}", file=sys.stderr)
                failed = True
    if include_gzh:
        try:
            executable = Path(install_gzh())
            print(f"{'gzh':<18} {'CLI':<23} installed ({executable})")
            warn_if_gzh_not_on_path(executable)
        except (InstallError, OSError, subprocess.CalledProcessError) as exc:
            print(f"{'gzh':<18} {'CLI':<23} {exc}", file=sys.stderr)
            failed = True
    return int(failed)


def selected_skill_targets(clients: list[str], scan_all: bool,
                           records: dict[Path, dict]) -> dict[Path, list[str]]:
    selected = set(clients)
    targets = {
        base: record["clients"]
        for base, record in records.items()
        if scan_all or selected.intersection(record["clients"])
    }
    recorded_clients = {
        client for record in records.values() for client in record["clients"]}
    missing_clients = [
        client for client in clients if client not in recorded_clients]
    for base, consumers in destinations(missing_clients).items():
        targets[absolute_path(base)] = consumers
    for base, record in records.items():
        if scan_all or selected.intersection(record["clients"]):
            targets.setdefault(base, record["clients"])
    if scan_all:
        for candidate in all_known_destinations():
            base = absolute_path(candidate)
            if base in targets:
                continue
            if any(path_present(base / name) for name in SKILL_NAMES):
                targets[base] = inferred_clients(base) or ["detected"]
    return targets


def run_status(clients: list[str], include_skills: bool,
               include_gzh: bool, scan_all: bool = False) -> int:
    failed = False
    if include_skills:
        records = read_installation_state()
        targets = selected_skill_targets(clients, scan_all, records)
        duplicate_bases = opencode_duplicate_bases(clients, records)
        if len(duplicate_bases) > 1:
            print(
                "error: duplicate OpenCode skills are discoverable from: "
                + ", ".join(str(path) for path in duplicate_bases),
                file=sys.stderr)
            failed = True
        for base, consumers in targets.items():
            label = "+".join(consumers)
            record = records.get(base)
            names = sorted(set(SKILL_NAMES).union(
                record["skills"] if record else ()))
            for name in names:
                if record and name in record["skills"]:
                    state, current = recorded_skill_status(record, name)
                else:
                    state, current = skill_status(SKILLS_ROOT / name, base / name)
                print(f"{label:<18} {name:<23} {state} ({base / name})")
                failed = failed or not current
    if include_gzh:
        state, current = gzh_status()
        print(f"{'gzh':<18} {'CLI':<23} {state}")
        failed = failed or not current
        if current:
            warn_if_gzh_not_on_path(gzh_paths()[1])
    return int(failed)


def run_uninstall(clients: list[str], include_skills: bool,
                  include_gzh: bool, scan_all: bool = False) -> int:
    failed = False
    if include_skills:
        records = read_installation_state()
        targets = selected_skill_targets(clients, scan_all, records)
        selected = set(clients)
        planned: list[tuple[Path, list[str], dict, dict[str, bool]]] = []
        absent: list[tuple[Path, list[str]]] = []
        conflicts: list[tuple[str, str]] = []
        for base, consumers in targets.items():
            label = "+".join(consumers)
            try:
                record = records.get(base) or legacy_skill_bundle(base)
                if record is None:
                    for name in SKILL_NAMES:
                        destination = base / name
                        if path_present(destination):
                            conflicts.append((
                                label,
                                f"refusing to remove unowned path: {destination}"))
                    absent.append((base, consumers))
                    continue
                remaining = set(record["clients"]) - selected
                if not scan_all and remaining:
                    requested = ", ".join(sorted(selected))
                    retained = ", ".join(sorted(remaining))
                    raise InstallError(
                        f"refusing partial uninstall at {base}: selecting {requested} "
                        f"would remove skills shared with {retained}; select every "
                        "recorded client together")
                validate_remove_skill_bundle(record)
                present = {
                    name: path_present(base / name) for name in record["skills"]
                }
                planned.append((base, consumers, record, present))
            except (InstallError, OSError) as exc:
                conflicts.append((label, str(exc)))
        if conflicts:
            for label, conflict in conflicts:
                print(f"{label:<18} {'bundle':<23} {conflict}", file=sys.stderr)
            return 1
        for base, consumers in absent:
            label = "+".join(consumers)
            for name in SKILL_NAMES:
                print(f"{label:<18} {name:<23} not installed")
        for _base, consumers, record, present in planned:
            label = "+".join(consumers)
            try:
                remove_skill_bundle(records, record)
                for name in record["skills"]:
                    state = "removed" if present[name] else "not installed"
                    print(f"{label:<18} {name:<23} {state}")
            except (InstallError, OSError) as exc:
                print(f"{label:<18} {'bundle':<23} {exc}", file=sys.stderr)
                failed = True
    if include_gzh:
        try:
            print(f"{'gzh':<18} {'CLI':<23} {uninstall_gzh()}")
        except InstallError as exc:
            print(f"{'gzh':<18} {'CLI':<23} {exc}", file=sys.stderr)
            failed = True
    return int(failed)


def refresh_installed() -> int:
    validate_sources()
    found = failed = False
    records = read_installation_state()
    bases = list(records)
    for candidate in all_known_destinations():
        base = absolute_path(candidate)
        if base not in records and base not in bases:
            bases.append(base)
    for base in bases:
        record = records.get(base)
        try:
            if record is None:
                record = legacy_skill_bundle(base)
            if record is None:
                unowned = [name for name in SKILL_NAMES if path_present(base / name)]
                for name in unowned:
                    print(
                        f"skill              {name:<23} unowned; skipped ({base / name})",
                        file=sys.stderr)
                failed = failed or bool(unowned)
                continue
            found = True
            new_record = skill_bundle_record(
                base, record["clients"], record["mode"], SKILL_NAMES)
            retired = sorted(set(record["skills"]) - set(SKILL_NAMES))
            synchronize_skill_bundle(records, record, new_record)
            for name in SKILL_NAMES:
                print(
                    f"skill              {name:<23} {record['mode']}, current "
                    f"({base / name})")
            for name in retired:
                print(f"skill              {name:<23} removed")
        except (InstallError, OSError) as exc:
            print(f"skill              {'bundle':<23} {exc}", file=sys.stderr)
            failed = True
    install_root, executable = gzh_paths()
    if install_root.exists() or executable.exists() or executable.is_symlink():
        found = True
        try:
            print(f"{'gzh':<18} {'CLI':<23} installed ({install_gzh()})")
        except (InstallError, OSError, subprocess.CalledProcessError) as exc:
            print(f"{'gzh':<18} {'CLI':<23} {exc}", file=sys.stderr)
            failed = True
    if not found:
        print("no managed installation found", file=sys.stderr)
        return 1
    return int(failed)


def client_name(value: str) -> str:
    if value not in CLIENT_NAMES:
        choices = ", ".join(sorted(CLIENT_NAMES))
        raise argparse.ArgumentTypeError(f"must be one of: {choices}")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "clients", nargs="*", type=client_name,
        metavar="{claude,codex,opencode}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--copy", action="store_const", const="copy", dest="mode")
    mode.add_argument("--link", action="store_const", const="link", dest="mode")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--status", action="store_true")
    operation.add_argument("--uninstall", action="store_true")
    operation.add_argument("--refresh-installed", action="store_true")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--skills-only", action="store_true")
    scope.add_argument("--gzh-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.refresh_installed:
        if args.clients or args.mode or args.skills_only or args.gzh_only:
            raise InstallError("--refresh-installed does not accept target or mode options")
        with installation_mutation_lock():
            return refresh_installed()
    clients = args.clients or ["codex", "opencode"]
    scan_all = not args.clients
    include_skills = not args.gzh_only
    include_gzh = not args.skills_only
    if (args.status or args.uninstall) and args.mode:
        raise InstallError("--copy and --link apply only to installation")
    if args.status:
        return run_status(clients, include_skills, include_gzh, scan_all=scan_all)
    if args.uninstall:
        with installation_mutation_lock():
            return run_uninstall(
                clients, include_skills, include_gzh, scan_all=scan_all)
    with installation_mutation_lock():
        return run_install(clients, args.mode or "link", include_skills, include_gzh)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
