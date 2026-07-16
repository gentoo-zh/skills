from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

SEVERITY_ORDER = {"error": 40, "warning": 30, "info": 20, "style": 10}

# a real browser UA: bare curl gets rate-limited/403'd by GitHub far more than a browser,
# which is a big source of the DeadUrl false positives this reverify exists to filter.
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _flatten(parsed: list) -> list[dict]:
    out = []
    for block in parsed or []:
        for r in block.get("results", []):
            out.append(r)
    return out


def run_pkgcheck(path: Path, min_severity: str = "warning",
                 net: bool = False, runner=subprocess.run) -> dict:
    args = ["pkgcheck", "scan", "--format", "json"]
    if net:  # enables the DeadUrl/RedirectedUrl network keychecks
        args.append("--net")
    args.append(str(path))
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


def run_pkgcheck_commits(cwd: Path, net: bool = True,
                         runner=subprocess.run) -> dict:
    """Reproduce the pre-PR gate `pkgcheck scan --commits --net` (overlay AGENTS.md /
    autobump.sh:661): NO path target -- --commits derives its atoms from the git diff of
    the uncommitted/unpushed commit -- run from the overlay root. Returns flattened
    results; pair with reverify_url_findings() to drop rate-limit false positives."""
    args = ["pkgcheck", "scan", "--commits", "--format", "json"]
    if net:
        args.append("--net")
    proc = runner(args, cwd=str(cwd), capture_output=True, text=True)
    try:
        parsed = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        parsed = []
    return {"ok": proc.returncode == 0, "results": _flatten(parsed),
            "raw_returncode": proc.returncode}


def reverify_url_findings(results: list[dict], runner=subprocess.run) -> dict:
    """Re-check DeadUrl/RedirectedUrl findings that reference SRC_URI, because a
    rate-limited `pkgcheck --net` over-reports GitHub (memory dead-upstream-audit:
    78/88 flagged were false positives). Only SRC_URI matters -- a HOMEPAGE DeadUrl does
    not block install. A URL is 'confirmed' dead only if a browser-UA ranged GET returns
    non-2xx/3xx. Note: 403 is usually auth-gated / RESTRICT=fetch by design (route to a
    human, not auto-dead); a fetch-restricted package emits MissingUri, not DeadUrl."""
    confirmed, transient = [], []
    for r in results:
        if r.get("code") not in ("DeadUrl", "RedirectedUrl"):
            continue
        blob = json.dumps(r, ensure_ascii=False)
        if "SRC_URI" not in blob:  # HOMEPAGE-only DeadUrl does not block a bump
            continue
        for url in dict.fromkeys(_URL_RE.findall(blob)):
            proc = runner(
                ["curl", "-r", "0-0", "-sL", "-A", _BROWSER_UA, "--retry", "2",
                 "--max-time", "30", "-o", "/dev/null", "-w", "%{http_code}", url],
                capture_output=True, text=True)
            code = (proc.stdout or "").strip()[-3:]
            entry = {"url": url, "http_code": code, "finding": r.get("code")}
            (transient if code.startswith(("2", "3")) else confirmed).append(entry)
    return {"confirmed": confirmed, "transient": transient,
            "checked": len(confirmed) + len(transient)}
