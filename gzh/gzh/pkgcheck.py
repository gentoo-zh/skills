from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from gzh.repo import find_canonical_remote

# pkgcheck severities, highest to lowest. Used to scope the pass/fail gate via `--exit`
# (JsonStream output carries no severity field, so the gate cannot be derived from the
# result objects; pkgcheck's own exit status is the source of truth).
SEVERITIES = ("error", "warning", "style", "info")

# a real browser UA: bare curl gets rate-limited/403'd by GitHub far more than a browser,
# which is a big source of the DeadUrl false positives this reverify exists to filter.
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
# HTTP codes that mean the file is genuinely gone (vs 401/403/429/000 = auth-gated or
# rate-limited, which are inconclusive and go to a human, not marked dead).
_TRULY_DEAD = {"404", "410"}


def _parse_ndjson(text: str) -> list[dict]:
    """pkgcheck's JsonStream reporter emits one flat JSON object per line (NDJSON), with
    the keyword name in `__class__`. Mirror it to `code` so callers can key on either."""
    out: list[dict] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            if "__class__" in obj and "code" not in obj:
                obj["code"] = obj["__class__"]
            out.append(obj)
    return out


def run_pkgcheck(path: Path, min_severity: str = "warning",
                 net: bool = False, runner=subprocess.run) -> dict:
    """Scan a package/path. `ok` is pkgcheck's own `--exit <min_severity>` verdict
    (non-zero when a result at or above min_severity exists, or on an internal error),
    NOT a severity filter over the results, because JsonStream carries no severity."""
    level = min_severity if min_severity in SEVERITIES else "warning"
    args = ["pkgcheck", "scan", "-R", "JsonStream", "--exit", level]
    if net:  # enables the DeadUrl/RedirectedUrl network keychecks
        args.append("--net")
    args.append(str(path))
    proc = runner(args, cwd=str(path) if path.is_dir() else None,
                  capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "results": _parse_ndjson(proc.stdout),
            "raw_returncode": proc.returncode}


def run_pkgcheck_commits(cwd: Path, net: bool = True, remote: str | None = None,
                         runner=subprocess.run) -> dict:
    """Run the pre-PR networked gate the overlay AGENTS.md prescribes.

    This is not a CI reproduction: the overlay's pkgcheck workflow runs offline, so this
    covers the segment CI never does. The merge-base range selects the targets, because a
    bare `--commits` compares against a fork's lagging `origin` and drags in unrelated
    packages; `--git-remote` only names the canonical source for the commit-only checks.
    Run from the overlay root. `--exit error` makes the exit status the gate. Pair with
    reverify_url_findings() to drop rate-limit false positives.
    """
    remote = remote or find_canonical_remote(cwd, runner=runner)
    # pkgcheck's git checks build their own cache from `<remote>/HEAD..HEAD`, independently
    # of any explicit range, so `<remote>/HEAD` has to resolve first.
    if runner(["git", "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD"],
              cwd=str(cwd), capture_output=True, text=True).returncode != 0:
        runner(["git", "remote", "set-head", remote, "master"],
               cwd=str(cwd), capture_output=True, text=True)
    proc = runner(["git", "merge-base", f"{remote}/master", "HEAD"],
                  cwd=str(cwd), capture_output=True, text=True)
    base = (proc.stdout or "").strip()
    # An empty base yields `..HEAD`, which git reads as `HEAD..HEAD`: pkgcheck then finds
    # no changed path, exits 0, and a red gate reports green. Fail loudly instead.
    if proc.returncode != 0 or not base:
        raise RuntimeError(
            f"no merge-base with {remote}/master; fetch {remote} before running the gate")
    args = ["pkgcheck", "scan", "--git-remote", remote, f"--commits={base}..HEAD",
            "-R", "JsonStream", "--exit", "error"]
    if net:
        args.append("--net")
    proc = runner(args, cwd=str(cwd), capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "results": _parse_ndjson(proc.stdout),
            "raw_returncode": proc.returncode}


def reverify_url_findings(results: list[dict], runner=subprocess.run) -> dict:
    """Re-check DeadUrl/RedirectedUrl findings that reference SRC_URI, because a
    rate-limited `pkgcheck --net` over-reports GitHub (memory dead-upstream-audit:
    78/88 flagged were false positives). Only SRC_URI matters -- a HOMEPAGE DeadUrl does
    not block install. Three buckets: transient (alive, 2xx/3xx), confirmed (genuinely
    gone, 404/410), needs_human (401/403/429/000: auth-gated by design, e.g. RESTRICT=fetch,
    or rate-limited; a fetch-restricted package emits MissingUri, not DeadUrl)."""
    confirmed, transient, needs_human = [], [], []
    for r in results:
        cls = r.get("__class__") or r.get("code")
        if cls not in ("DeadUrl", "RedirectedUrl"):
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
            entry = {"url": url, "http_code": code, "finding": cls}
            if code.startswith(("2", "3")):
                transient.append(entry)
            elif code in _TRULY_DEAD:
                confirmed.append(entry)
            else:
                needs_human.append(entry)
    return {"confirmed": confirmed, "transient": transient,
            "needs_human": needs_human,
            "checked": len(confirmed) + len(transient) + len(needs_human)}
