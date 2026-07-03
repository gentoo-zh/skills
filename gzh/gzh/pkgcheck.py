from __future__ import annotations

import json
import subprocess
from pathlib import Path

SEVERITY_ORDER = {"error": 40, "warning": 30, "info": 20, "style": 10}


def _flatten(parsed: list) -> list[dict]:
    out = []
    for block in parsed or []:
        for r in block.get("results", []):
            out.append(r)
    return out


def run_pkgcheck(path: Path, min_severity: str = "warning",
                 runner=subprocess.run) -> dict:
    args = ["pkgcheck", "scan", "--format", "json", str(path)]
    proc = runner(args, cwd=str(path) if path.is_dir() else None,
                  capture_output=True, text=True)
    try:
        parsed = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        parsed = []
    threshold = SEVERITY_ORDER.get(min_severity, 0)
    results = [r for r in _flatten(parsed)
               if SEVERITY_ORDER.get(r.get("severity", "info"), 0) >= threshold]
    has_error = any(r.get("severity") == "error" for r in results)
    return {"ok": not has_error, "results": results,
            "raw_returncode": proc.returncode}
