#!/usr/bin/env python3
"""Update a checkout safely, refresh installations, and audit references."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install.py"
SOURCE_MANAGER = (
    ROOT / ".agents" / "skills" / "gentoo-overlay-development" / "scripts"
    / "source_manager.py")
LESSON_LOOKUP = (
    ROOT / ".agents" / "skills" / "gzh-version-bump" / "scripts"
    / "lesson_lookup.py")
CANONICAL_SLUG = "gentoo-zh/skills"


def run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, check=check)


def github_slug(url: str) -> str | None:
    value = url.strip()
    if re.match(r"^[^/@:]+@github\.com:", value, flags=re.IGNORECASE):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        path = parsed.path
    return path.strip("/").removesuffix(".git") or None


def find_canonical_remote() -> str:
    names = subprocess.run(
        ["git", "remote"], cwd=ROOT, capture_output=True, text=True,
        check=True).stdout.splitlines()
    matches = []
    for name in names:
        fetch_url = subprocess.run(
            ["git", "remote", "get-url", name], cwd=ROOT,
            capture_output=True, text=True, check=True).stdout.strip()
        if (github_slug(fetch_url) or "").lower() == CANONICAL_SLUG:
            matches.append(name)
    if len(matches) != 1:
        detail = "none" if not matches else ", ".join(matches)
        raise RuntimeError(f"expected one canonical {CANONICAL_SLUG} remote; found {detail}")
    return matches[0]


def update_checkout() -> None:
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT,
        capture_output=True, text=True, check=True)
    if status.stdout.strip():
        raise RuntimeError("checkout has local changes; refusing to pull")
    branch = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"], cwd=ROOT,
        capture_output=True, text=True)
    if branch.returncode != 0:
        raise RuntimeError("checkout is detached; refusing to pull")
    if branch.stdout.strip() != "master":
        raise RuntimeError(
            f"checkout is on {branch.stdout.strip() or 'an unknown branch'}; "
            "only master may be updated")
    remote = find_canonical_remote()
    run(["git", "fetch", remote, "master"])
    before = subprocess.run(
        ["git", "rev-list", "--left-right", "--count",
         f"HEAD...{remote}/master"], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout.split()
    if len(before) != 2:
        raise RuntimeError("cannot determine master synchronization state")
    if before[0] != "0":
        raise RuntimeError(
            f"local master has {before[0]} commit(s) absent from {remote}/master; "
            "refusing to merge")
    run(["git", "merge", "--ff-only", f"{remote}/master"])
    after = subprocess.run(
        ["git", "rev-list", "--left-right", "--count",
         f"HEAD...{remote}/master"], cwd=ROOT, capture_output=True,
        text=True, check=True).stdout.split()
    if after != ["0", "0"]:
        raise RuntimeError("master is not synchronized after the fast-forward")


def audit_references() -> int:
    source = run(
        [sys.executable, str(SOURCE_MANAGER), "audit", "--all-scopes",
         "--fail-on-drift"],
        check=False)
    lessons = run(
        [sys.executable, str(LESSON_LOOKUP), "--refresh", "--stats"],
        check=False)
    return int(source.returncode != 0 or lessons.returncode != 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installed-only", action="store_true",
        help="refresh managed installations without pulling this checkout")
    parser.add_argument(
        "--references", action="store_true",
        help="audit registered sources and report lesson corpus statistics")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not args.installed_only:
            update_checkout()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    install = run(
        [sys.executable, str(INSTALLER), "--refresh-installed"], check=False)
    reference_status = audit_references() if args.references else 0
    return int(install.returncode != 0 or reference_status != 0)


if __name__ == "__main__":
    raise SystemExit(main())
