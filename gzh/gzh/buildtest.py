from __future__ import annotations

import subprocess
from pathlib import Path

PHASES = {
    "quick": ["clean", "unpack", "prepare", "configure"],
    "full": ["clean", "unpack", "prepare", "configure", "compile", "install"],
}


def run_build_test(ebuild: Path, level: str = "full",
                   runner=subprocess.run) -> dict:
    if level == "none":
        return {"ok": True, "level": level, "skipped": True,
                "reason": "level=none", "failed_phase": None,
                "log_path": None, "stdout": "", "stderr": "",
                "returncode": 0}
    phases = PHASES[level]
    failed_phase = None
    stdout_parts, stderr_parts = [], []
    rc = 0
    for phase in phases:
        args = ["ebuild", str(ebuild), phase]
        proc = runner(args, capture_output=True, text=True)
        stdout_parts.append(proc.stdout)
        stderr_parts.append(proc.stderr)
        rc = proc.returncode
        if proc.returncode != 0:
            failed_phase = phase
            break
    log_path = None
    # Portage writes temp logs; best-effort pointer (not guaranteed to exist)
    return {"ok": failed_phase is None, "level": level,
            "failed_phase": failed_phase, "log_path": log_path,
            "stdout": "".join(stdout_parts), "stderr": "".join(stderr_parts),
            "returncode": rc, "skipped": False}
