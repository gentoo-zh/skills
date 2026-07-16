from __future__ import annotations

import subprocess
from pathlib import Path


def run_commit(paths: list[Path], cwd: Path,
               message: str | None = None,
               runner=subprocess.run) -> dict:
    for p in paths:
        runner(["git", "add", str(p)], cwd=cwd, capture_output=True, text=True)
    # gentoo-zh commit policy requires DCO sign-off (overlay AGENTS.md). GPG signing
    # is intentionally NOT forced here: the environment may lack a key, and the overlay
    # policy itself defines a no-GPG fallback. gzh pkgcheck is a separate hard gate, so
    # --scan is left at pkgdev's default.
    args = ["pkgdev", "commit", "--signoff"]
    if message:
        args += ["--message", message]
    args += [str(p) for p in paths]
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
