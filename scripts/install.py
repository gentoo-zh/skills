#!/usr/bin/env python3
"""Install the gentoo-zh skills and gzh CLI for supported agent clients."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILLS_ROOT = ROOT / ".agents" / "skills"
SKILL_NAMES = ("gzh-version-bump", "gzh-bump-from-issues")
INSTALLER_ID = "gentoo-zh/skills"
SKILL_MARKER = ".gzh-skill-install.json"
GZH_MARKER = ".gzh-install.json"
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
    paths = [
        expand_env_path("CLAUDE_CONFIG_DIR", home / ".claude") / "skills",
        expand_env_path(
            "XDG_CONFIG_HOME", home / ".config") / "opencode" / "skills",
        home / ".agents" / "skills",
    ]
    return list(dict.fromkeys(paths))


def gzh_paths() -> tuple[Path, Path]:
    home = Path.home()
    data_home = expand_env_path("XDG_DATA_HOME", home / ".local" / "share")
    install_root = expand_env_path(
        "GZH_INSTALL_ROOT", data_home / "gentoo-zh-skills" / "gzh")
    bin_dir = expand_env_path("GZH_BIN_DIR", home / ".local" / "bin")
    return install_root, bin_dir / "gzh"


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
             "--disable-pip-version-check", "--no-build-isolation",
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
    targets = destinations(clients)
    conflicts = []
    if include_skills:
        for base in targets:
            for name in SKILL_NAMES:
                destination = base / name
                if ((destination.exists() or destination.is_symlink())
                        and not owned_skill(destination, SKILLS_ROOT / name)[0]):
                    conflicts.append(f"refusing to replace unowned path: {destination}")
        if "opencode" in clients:
            planned_bases = set(targets)
            planned_discovery = planned_bases.intersection(
                opencode_discovery_destinations())
            if len(planned_discovery) > 1:
                paths = ", ".join(str(path) for path in sorted(planned_discovery))
                conflicts.append(
                    f"selected clients would create duplicate OpenCode skills: {paths}")
            for base in opencode_discovery_destinations():
                if base in planned_bases:
                    continue
                for name in SKILL_NAMES:
                    alternate = base / name
                    if alternate.exists() or alternate.is_symlink():
                        conflicts.append(
                            f"duplicate OpenCode skill exists outside the target: {alternate}")
        if "codex" in clients:
            planned_bases = set(targets)
            for base in codex_discovery_destinations():
                if base in planned_bases:
                    continue
                for name in SKILL_NAMES:
                    alternate = base / name
                    if alternate.exists() or alternate.is_symlink():
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
            for name in SKILL_NAMES:
                source = SKILLS_ROOT / name
                destination = base / name
                try:
                    state = install_skill(source, destination, mode)
                    print(f"{label:<18} {name:<23} {state} ({destination})")
                except (InstallError, OSError) as exc:
                    print(f"{label:<18} {name:<23} {exc}", file=sys.stderr)
                    failed = True
    if include_gzh:
        try:
            print(f"{'gzh':<18} {'CLI':<23} installed ({install_gzh()})")
        except (InstallError, OSError, subprocess.CalledProcessError) as exc:
            print(f"{'gzh':<18} {'CLI':<23} {exc}", file=sys.stderr)
            failed = True
    return int(failed)


def _extra_detected_destinations(planned: dict[Path, list[str]]) -> dict[Path, list[str]]:
    extras = {}
    for base in all_known_destinations():
        if base in planned:
            continue
        if any((base / name).exists() or (base / name).is_symlink()
               for name in SKILL_NAMES):
            extras[base] = ["detected"]
    return extras


def run_status(clients: list[str], include_skills: bool,
               include_gzh: bool, scan_all: bool = False) -> int:
    failed = False
    if include_skills:
        targets = destinations(clients)
        if scan_all:
            targets.update(_extra_detected_destinations(targets))
        for base, consumers in targets.items():
            label = "+".join(consumers)
            for name in SKILL_NAMES:
                state, current = skill_status(SKILLS_ROOT / name, base / name)
                print(f"{label:<18} {name:<23} {state} ({base / name})")
                failed = failed or not current
    if include_gzh:
        state, current = gzh_status()
        print(f"{'gzh':<18} {'CLI':<23} {state}")
        failed = failed or not current
    return int(failed)


def run_uninstall(clients: list[str], include_skills: bool,
                  include_gzh: bool, scan_all: bool = False) -> int:
    failed = False
    if include_skills:
        targets = destinations(clients)
        if scan_all:
            targets.update(_extra_detected_destinations(targets))
        for base, consumers in targets.items():
            label = "+".join(consumers)
            for name in SKILL_NAMES:
                destination = base / name
                if not destination.exists() and not destination.is_symlink():
                    print(f"{label:<18} {name:<23} not installed")
                    continue
                if not owned_skill(destination, SKILLS_ROOT / name)[0]:
                    print(f"{label:<18} {name:<23} refusing to remove unowned path: {destination}",
                          file=sys.stderr)
                    failed = True
                    continue
                remove_path(destination)
                print(f"{label:<18} {name:<23} removed")
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
    for base in all_known_destinations():
        for name in SKILL_NAMES:
            source = SKILLS_ROOT / name
            destination = base / name
            if not destination.exists() and not destination.is_symlink():
                continue
            found = True
            owned, mode = owned_skill(destination, source)
            if not owned:
                print(f"skill              {name:<23} unowned; skipped ({destination})",
                      file=sys.stderr)
                failed = True
                continue
            if mode == "copy":
                install_skill(source, destination, "copy")
            print(f"skill              {name:<23} {mode}, current ({destination})")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("clients", nargs="*", choices=("claude", "codex", "opencode"))
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
        return run_uninstall(clients, include_skills, include_gzh, scan_all=scan_all)
    return run_install(clients, args.mode or "link", include_skills, include_gzh)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
