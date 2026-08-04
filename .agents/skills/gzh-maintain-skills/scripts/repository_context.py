#!/usr/bin/env python3
"""Resolve a live gentoo-zh/skills checkout independently of skill location."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse


REQUIRED_PATHS = (
    "RELEASING.md",
    "scripts/validate_repository.py",
    "scripts/release_check.py",
    ".agents/skills/gzh-maintain-skills/SKILL.md",
)
CANONICAL_SLUG = "gentoo-zh/skills"


def github_slug(url: str) -> str | None:
    value = url.strip()
    if re.match(r"^[^/@:]+@github\.com:", value, flags=re.IGNORECASE):
        path = value.split(":", 1)[1]
    else:
        parsed = urlparse(value)
        if (parsed.hostname or "").lower() != "github.com":
            return None
        path = parsed.path
    return path.strip("/").removesuffix(".git").lower() or None


def repository_root(start: Path | None = None) -> Path:
    directory = (start or Path.cwd()).resolve()
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or "not inside a Git checkout"
        raise RuntimeError(f"cannot resolve skills repository: {detail}")
    root = Path(result.stdout.strip()).resolve()
    missing = [relative for relative in REQUIRED_PATHS
               if not (root / relative).is_file()]
    if missing:
        raise RuntimeError(
            "current Git checkout is not a complete gentoo-zh/skills repository: "
            + ", ".join(missing))
    remotes = subprocess.run(
        ["git", "-C", str(root), "remote"], check=False,
        capture_output=True, text=True)
    if remotes.returncode != 0:
        raise RuntimeError("cannot list repository remotes")
    matches = []
    for name in remotes.stdout.splitlines():
        remote = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", name],
            check=False, capture_output=True, text=True)
        if remote.returncode == 0 and github_slug(remote.stdout) == CANONICAL_SLUG:
            matches.append(name)
    if len(matches) != 1:
        detail = "none" if not matches else ", ".join(matches)
        raise RuntimeError(
            f"expected one canonical {CANONICAL_SLUG} remote; found {detail}")
    return root
