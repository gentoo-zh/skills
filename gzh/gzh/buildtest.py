from __future__ import annotations

import subprocess
from pathlib import Path

PHASES = {
    "quick": ["clean", "unpack", "prepare", "configure"],
    "full": ["clean", "unpack", "prepare", "configure", "compile", "install"],
}

# QA notices that the emerge-on-PR elog gate flags but a bare `ebuild install` cannot
# adjudicate: an unresolved soname often just means the RDEPEND provider is not on the
# local box and will resolve in CI. Defer it to the dependency-resolving emerge gate
# (see finish-pipeline.md step 4) instead of failing the local build test on it.
_DEFERRED_QA = ("Unresolved soname",)


def scan_qa_notices(text: str) -> list[str]:
    """Advisory 'QA Notice' lines from a build stream, minus the deferred classes.

    Portage prints eqawarn/QA Notice to stderr, so callers should pass the COMBINED
    stdout+stderr (mirroring autobump.sh's `> build.log 2>&1`). This never changes the
    pass/fail verdict; it only surfaces notices for the -bin/prebuilt path to inspect.
    """
    return [ln for ln in (text or "").splitlines()
            if "QA Notice" in ln and not any(d in ln for d in _DEFERRED_QA)]


def run_build_test(ebuild: Path, level: str = "full",
                   runner=subprocess.run) -> dict:
    if level == "none":
        return {"ok": True, "level": level, "skipped": True,
                "reason": "level=none", "failed_phase": None,
                "log_path": None, "stdout": "", "stderr": "",
                "returncode": 0, "qa_notices": []}
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
    _qa_text = "".join(stdout_parts) + "".join(stderr_parts)
    return {"ok": failed_phase is None, "level": level,
            "failed_phase": failed_phase, "log_path": log_path,
            "stdout": "".join(stdout_parts), "stderr": "".join(stderr_parts),
            "returncode": rc, "skipped": False,
            "qa_notices": scan_qa_notices(_qa_text)}
