from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

_CANONICAL_SLUGS = ("gentoo-zh/overlay", "microcai/gentoo-zh")
_PORTAGE_REPOS = Path("/var/db/repos")


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


def is_portage_synced_repo(root: Path) -> bool:
    root = Path(root).expanduser().resolve()
    return root == _PORTAGE_REPOS or _PORTAGE_REPOS in root.parents


def validate_overlay_root(root: Path) -> Path:
    """Return a verified gentoo-zh overlay development checkout."""
    root = Path(root).expanduser().resolve()
    if is_portage_synced_repo(root):
        raise RuntimeError(f"refusing to use a Portage-synced repository: {root}")
    repo_name = root / "profiles" / "repo_name"
    if not root.is_dir() or not repo_name.is_file():
        raise RuntimeError(f"not a gentoo-zh overlay checkout: {root}")
    if repo_name.read_text(encoding="utf-8").strip() != "gentoo-zh":
        raise RuntimeError(f"unexpected profiles/repo_name in {root}")
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=root,
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"overlay is not a Git development checkout: {root}")
    if Path(proc.stdout.strip()).resolve() != root:
        raise RuntimeError(f"overlay path is not the Git worktree root: {root}")
    return root


def find_overlay_root(start: Path | None = None) -> Path:
    env = os.environ.get("GZH_OVERLAY_DIR")
    if env:
        return validate_overlay_root(Path(env))
    start = (start or Path.cwd()).resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"not inside a git repo: {start}") from exc
    return validate_overlay_root(Path(out.stdout.strip()))


def find_canonical_remote(cwd: Path, runner=subprocess.run) -> str:
    """Name of the remote pointing at gentoo-zh/overlay.

    Usually `upstream` on a fork clone and `origin` on a direct clone, so it must be
    discovered rather than assumed. Prefer the current repository over the accepted
    legacy repository, then conventional remote names when duplicate aliases exist.
    """
    proc = runner(["git", "remote", "-v"], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"cannot list remotes in {cwd}: {proc.stderr.strip()}")
    ranks = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2 or (len(parts) >= 3 and parts[2] != "(fetch)"):
            continue
        slug = github_slug(parts[1])
        for rank, canonical_slug in enumerate(_CANONICAL_SLUGS):
            if slug == canonical_slug:
                ranks[parts[0]] = min(rank, ranks.get(parts[0], rank))
                break
    if not ranks:
        raise RuntimeError(
            "no canonical gentoo-zh/overlay or microcai/gentoo-zh remote found")
    best_rank = min(ranks.values())
    names = {name for name, rank in ranks.items() if rank == best_rank}
    for preferred in ("upstream", "origin", "canonical"):
        if preferred in names:
            return preferred
    return sorted(names)[0]


def validate_canonical_remote(cwd: Path, remote: str,
                              runner=subprocess.run) -> str:
    """Reject a named remote unless its primary fetch URL is canonical."""
    proc = runner(["git", "remote", "get-url", remote], cwd=str(cwd),
                  capture_output=True, text=True)
    url = (proc.stdout or "").strip()
    if proc.returncode != 0 or not url:
        raise RuntimeError(
            f"cannot read fetch URL for remote {remote!r}: {proc.stderr.strip()}")
    if github_slug(url) not in _CANONICAL_SLUGS:
        raise RuntimeError(
            f"remote {remote!r} does not point to gentoo-zh/overlay or "
            "microcai/gentoo-zh")
    return remote
