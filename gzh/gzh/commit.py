from __future__ import annotations

import subprocess
from pathlib import Path


def has_signing_key(cwd: Path, runner=subprocess.run) -> bool:
    """True when git has a signing key configured, so --gpg-sign can succeed."""
    proc = runner(["git", "config", "--get", "user.signingkey"],
                  cwd=cwd, capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def run_commit(paths: list[Path], cwd: Path,
               message: str | None = None,
               runner=subprocess.run) -> dict:
    for p in paths:
        runner(["git", "add", str(p)], cwd=cwd, capture_output=True, text=True)
    # overlay AGENTS.md: pkgdev commit --scan false --signoff --gpg-sign, and omit
    # --gpg-sign where GPG is unavailable. --scan false because gzh pkgcheck already
    # ran as its own hard gate.
    args = ["pkgdev", "commit", "--scan", "false", "--signoff"]
    if has_signing_key(cwd, runner=runner):
        args.append("--gpg-sign")
    if message:
        args += ["--message", message]
    args += [str(p) for p in paths]
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
