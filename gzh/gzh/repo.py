from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# Match either remote form, case-insensitively: git@github.com:gentoo-zh/overlay.git
# and https://github.com/gentoo-zh/overlay
_CANONICAL_RE = re.compile(r"github\.com[:/]gentoo-zh/overlay(\.git)?/?$", re.I)


def find_overlay_root(start: Path | None = None) -> Path:
    env = os.environ.get("GZH_OVERLAY_DIR")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"GZH_OVERLAY_DIR not a directory: {root}")
        return root
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
    return Path(out.stdout.strip())


def find_canonical_remote(cwd: Path, runner=subprocess.run) -> str:
    """Name of the remote pointing at gentoo-zh/overlay.

    Usually `upstream` on a fork clone and `origin` on a direct clone, so it must be
    discovered rather than assumed. Raises when zero or several remotes match, because
    guessing here would scope a gate against the wrong repository.
    """
    proc = runner(["git", "remote", "-v"], cwd=str(cwd), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"cannot list remotes in {cwd}: {proc.stderr.strip()}")
    names = []
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and _CANONICAL_RE.search(parts[1]) and parts[0] not in names:
            names.append(parts[0])
    if len(names) != 1:
        raise RuntimeError(
            f"expected exactly one remote for gentoo-zh/overlay, found {names or 'none'}")
    return names[0]
