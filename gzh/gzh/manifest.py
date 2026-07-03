from __future__ import annotations

import subprocess
from pathlib import Path


def run_manifest(ebuild: Path, cwd: Path | None = None,
                 runner=subprocess.run) -> dict:
    args = ["pkgdev", "manifest", "--force", str(ebuild)]
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    return {"ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
