from __future__ import annotations

import os
import subprocess
from pathlib import Path


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
