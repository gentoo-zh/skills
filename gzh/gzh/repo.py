from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

_CANONICAL_SLUGS = ("gentoo-zh/overlay", "microcai/gentoo-zh")
_PORTAGE_REPOS = Path("/var/db/repos")
_DEFAULT_BRANCH = "master"


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


def canonical_remote_candidates(cwd: Path, runner=subprocess.run) -> list[dict]:
    """Return canonical fetch remotes with their tracked default-branch OIDs."""
    proc = runner(["git", "remote", "-v"], cwd=str(cwd),
                  capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"cannot list remotes in {cwd}: {proc.stderr.strip()}")
    matches: dict[str, dict] = {}
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 2 or (len(parts) >= 3 and parts[2] != "(fetch)"):
            continue
        slug = github_slug(parts[1])
        for rank, canonical_slug in enumerate(_CANONICAL_SLUGS):
            if slug == canonical_slug:
                current = matches.get(parts[0])
                if current is None or rank < current["priority"]:
                    matches[parts[0]] = {
                        "name": parts[0], "url": parts[1],
                        "slug": slug, "priority": rank,
                    }
                break
    for name, item in matches.items():
        ref = f"refs/remotes/{name}/{_DEFAULT_BRANCH}"
        oid = runner(["git", "rev-parse", "--verify", ref], cwd=str(cwd),
                     capture_output=True, text=True)
        value = (oid.stdout or "").strip()
        item["default_branch"] = _DEFAULT_BRANCH
        item["oid"] = value if oid.returncode == 0 and value else None
    return sorted(matches.values(), key=lambda item: (item["priority"], item["name"]))


def find_canonical_remote(cwd: Path, runner=subprocess.run) -> str:
    """Name of the remote pointing at gentoo-zh/overlay.

    Usually `upstream` on a fork clone and `origin` on a direct clone, so it must be
    discovered rather than assumed. Prefer the current repository over the accepted
    legacy repository, then conventional remote names when duplicate aliases exist.
    """
    candidates = canonical_remote_candidates(cwd, runner=runner)
    if not candidates:
        raise RuntimeError(
            "no canonical gentoo-zh/overlay or microcai/gentoo-zh remote found")
    best_rank = min(item["priority"] for item in candidates)
    selected = [item for item in candidates if item["priority"] == best_rank]
    if len(selected) > 1:
        oids = {item["oid"] for item in selected}
        if None in oids or len(oids) != 1:
            detail = ", ".join(
                f"{item['name']}={item['oid'] or 'missing'}" for item in selected)
            raise RuntimeError(
                "canonical remote aliases do not have one verified master OID; "
                f"select and fetch one explicitly: {detail}")
    names = {item["name"] for item in selected}
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


def fetch_canonical_remote(cwd: Path, remote: str,
                           runner=subprocess.run) -> dict:
    """Fetch one explicitly selected canonical remote and bind its HEAD to master."""
    validate_canonical_remote(cwd, remote, runner=runner)

    def oid() -> str | None:
        proc = runner(
            ["git", "rev-parse", "--verify",
             f"refs/remotes/{remote}/{_DEFAULT_BRANCH}"],
            cwd=str(cwd), capture_output=True, text=True)
        value = (proc.stdout or "").strip()
        return value if proc.returncode == 0 and value else None

    before = oid()
    fetch = runner(["git", "fetch", remote, _DEFAULT_BRANCH], cwd=str(cwd),
                   capture_output=True, text=True)
    if fetch.returncode != 0:
        raise RuntimeError(
            f"cannot fetch {remote}/{_DEFAULT_BRANCH}: {fetch.stderr.strip()}")
    after = oid()
    if after is None:
        raise RuntimeError(
            f"fetch completed without {remote}/{_DEFAULT_BRANCH}")
    set_head = runner(
        ["git", "remote", "set-head", remote, _DEFAULT_BRANCH], cwd=str(cwd),
        capture_output=True, text=True)
    if set_head.returncode != 0:
        raise RuntimeError(
            f"cannot set {remote}/HEAD to {_DEFAULT_BRANCH}: {set_head.stderr.strip()}")
    return {
        "after_oid": after,
        "before_oid": before,
        "changed": before != after,
        "complete": True,
        "default_branch": _DEFAULT_BRANCH,
        "fetch_command": ["git", "fetch", remote, _DEFAULT_BRANCH],
        "ok": True,
        "remote": remote,
        "stderr": fetch.stderr,
        "stdout": fetch.stdout,
        "truncated": False,
    }
