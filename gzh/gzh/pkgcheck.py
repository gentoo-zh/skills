from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from gzh.qa_evidence import (DEFAULT_MAX_OUTPUT_BYTES, DEFAULT_TIMEOUT,
                             identify_input, read_tool_version,
                             run_evidence_command)
from gzh.repo import find_canonical_remote, validate_canonical_remote

# pkgcheck severities, highest to lowest. Used to scope the pass/fail gate via `--exit`
# (JsonStream output carries no severity field, so the gate cannot be derived from the
# result objects; pkgcheck's own exit status is the source of truth).
SEVERITIES = ("error", "warning", "style", "info")

# Use a browser user agent so a follow-up request is less likely to be rejected solely
# because of a generic command-line client identifier.
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
# HTTP codes that mean the file is genuinely gone (vs 401/403/429/000 = auth-gated or
# rate-limited, which are inconclusive and go to a human, not marked dead).
_TRULY_DEAD = {"404", "410"}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_ndjson_evidence(text: str) -> tuple[list[dict], list[dict]]:
    """Parse JsonStream without discarding malformed or non-finding records."""
    out: list[dict] = []
    malformed: list[dict] = []
    for number, raw_line in enumerate((text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            malformed.append({
                "line": number,
                "reason": str(exc),
                "sha256": hashlib.sha256(line.encode()).hexdigest(),
                "preview": line[:512],
            })
            continue
        if (not isinstance(obj, dict)
                or not isinstance(obj.get("__class__"), str)
                or not obj["__class__"].strip()):
            malformed.append({
                "line": number,
                "reason": "record is not a pkgcheck finding object",
                "sha256": hashlib.sha256(line.encode()).hexdigest(),
                "preview": line[:512],
            })
            continue
        if "code" not in obj:
            obj["code"] = obj["__class__"]
        out.append(obj)
    return out, malformed


def _parse_ndjson(text: str) -> list[dict]:
    """Compatibility wrapper returning only valid findings."""
    return _parse_ndjson_evidence(text)[0]


def _scan_report(command: list[str], cwd: Path | None, input_path: Path,
                 runner, timeout: int, max_output_bytes: int) -> dict:
    version = read_tool_version(
        [command[0], "--version"], timeout=min(timeout, 30),
        max_output_bytes=1024, runner=runner)
    execution = run_evidence_command(
        command, cwd=cwd, timeout=timeout,
        max_output_bytes=max_output_bytes, runner=runner)
    results, malformed = _parse_ndjson_evidence(execution["stdout"])
    returncode = execution["returncode"]
    stream_complete = execution["complete"] and not execution["truncated"]
    verdict_complete = (returncode == 0
                        or (returncode == 1 and bool(results)))
    scan_complete = stream_complete and verdict_complete and not malformed
    complete = scan_complete and version["complete"]
    ok = stream_complete and returncode == 0 and not malformed
    truncated = (execution["truncated"]
                 or version["execution"]["truncated"])
    timed_out = (execution["timed_out"]
                 or version["execution"]["timed_out"])
    if truncated:
        state = "truncated"
    elif not complete:
        state = "incomplete"
    elif ok:
        state = "passed"
    else:
        state = "failed"
    errors = []
    if not version["complete"]:
        errors.append({
            "stage": "tool-version",
            "type": "IncompleteToolVersion",
            "message": "cannot obtain complete bounded pkgcheck version evidence",
        })
    if execution["error"]:
        errors.append({"stage": "execution", **execution["error"]})
    if malformed:
        errors.append({
            "stage": "parse",
            "type": "MalformedJsonStream",
            "message": f"pkgcheck emitted {len(malformed)} malformed record(s)",
        })
    if stream_complete and not verdict_complete:
        errors.append({
            "stage": "verdict",
            "type": "IncompletePkgcheckVerdict",
            "message": (
                f"pkgcheck return code {returncode} has no complete finding stream"),
        })
    return {
        # Keep the original keys stable for existing CLI and library callers.
        "ok": ok,
        "results": results,
        "raw_returncode": returncode,
        "complete": complete,
        "truncated": truncated,
        "skipped": False,
        "state": state,
        "command": command,
        "tool_version": version["version"],
        "tool_version_evidence": version,
        "input": identify_input(input_path),
        "duration_seconds": execution["duration_seconds"],
        "stderr": execution["stderr"],
        "malformed_output": malformed,
        "timed_out": timed_out,
        "execution": execution,
        "errors": errors,
    }


def run_pkgcheck(path: Path, min_severity: str = "warning",
                 net: bool = False, runner=subprocess.run,
                 timeout: int = DEFAULT_TIMEOUT,
                 max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> dict:
    """Scan a path and retain pkgcheck's verdict plus bounded execution evidence.

    For a complete JsonStream, `ok` follows pkgcheck's own `--exit` status. Malformed,
    timed-out, or truncated evidence is incomplete and cannot report success.
    """
    level = min_severity if min_severity in SEVERITIES else "warning"
    args = ["pkgcheck", "scan", "-R", "JsonStream", "--exit", level]
    if net:  # enables the DeadUrl/RedirectedUrl network keychecks
        args.append("--net")
    args.append(str(path))
    return _scan_report(
        args, path if path.is_dir() else None, path, runner, timeout,
        max_output_bytes)


def run_pkgcheck_commits(cwd: Path, net: bool = True, remote: str | None = None,
                         runner=subprocess.run, timeout: int = DEFAULT_TIMEOUT,
                         max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> dict:
    """Run the pre-PR networked gate the overlay AGENTS.md prescribes.

    This is not a CI reproduction: the overlay's pkgcheck workflow runs offline, so this
    covers the segment CI never does. The merge-base range selects the targets, because a
    bare `--commits` compares against a fork's lagging `origin` and drags in unrelated
    packages; `--git-remote` only names the canonical source for the commit-only checks.
    Run from the overlay root. `--exit error` makes the exit status the gate. Pair with
    reverify_url_findings() to classify individual URL responses for manual review.
    """
    if remote is None:
        remote = find_canonical_remote(cwd, runner=runner)
    else:
        remote = validate_canonical_remote(cwd, remote, runner=runner)
    # pkgcheck's git checks build their own cache from `<remote>/HEAD..HEAD`, independently
    # of any explicit range, so `<remote>/HEAD` has to resolve first.
    if runner(["git", "symbolic-ref", "-q", f"refs/remotes/{remote}/HEAD"],
              cwd=str(cwd), capture_output=True, text=True).returncode != 0:
        raise RuntimeError(
            f"canonical remote HEAD is unavailable for {remote}; fetch the remote and "
            "set its HEAD before running the gate")
    proc = runner(["git", "merge-base", f"{remote}/master", "HEAD"],
                  cwd=str(cwd), capture_output=True, text=True)
    base = (proc.stdout or "").strip()
    # An empty base yields `..HEAD`, which git reads as `HEAD..HEAD`: pkgcheck then finds
    # no changed path, exits 0, and a red gate reports green. Fail loudly instead.
    if proc.returncode != 0 or not base:
        raise RuntimeError(
            f"no merge-base with {remote}/master; fetch {remote} before running the gate")
    range_spec = f"{base}..HEAD"
    proc = runner(["git", "rev-list", "--count", range_spec], cwd=str(cwd),
                  capture_output=True, text=True)
    try:
        commit_count = int((proc.stdout or "").strip())
    except ValueError:
        commit_count = 0
    if proc.returncode != 0 or commit_count < 1:
        raise RuntimeError(
            f"no local commits in {range_spec}; the network gate cannot scan an empty range")
    args = ["pkgcheck", "scan", "--git-remote", remote, f"--commits={range_spec}",
            "-R", "JsonStream", "--exit", "error"]
    if net:
        args.append("--net")
    report = _scan_report(
        args, cwd, cwd, runner, timeout, max_output_bytes)
    report["commit_range"] = {
        "canonical_remote": remote,
        "merge_base": base,
        "range": range_spec,
        "commit_count": commit_count,
    }
    return report


def reverify_url_findings(results: list[dict], runner=subprocess.run) -> dict:
    """Classify follow-up responses for SRC_URI DeadUrl and RedirectedUrl findings.

    A successful response is transient, 404 or 410 is confirmed, and authentication,
    rate-limit, transport, or other responses require human review. HOMEPAGE findings do
    not establish whether a package distfile can be fetched and are left to the original
    pkgcheck result.
    """
    confirmed, redirected, transient, needs_human = [], [], [], []
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
            entry = {"url": url, "http_code": code, "finding": cls,
                     "transport_returncode": proc.returncode}
            if proc.returncode != 0:
                needs_human.append(entry)
            elif cls == "RedirectedUrl" and code.startswith(("2", "3")):
                redirected.append(entry)
            elif code.startswith(("2", "3")):
                transient.append(entry)
            elif code in _TRULY_DEAD:
                confirmed.append(entry)
            else:
                needs_human.append(entry)
    return {"confirmed": confirmed, "redirected": redirected,
            "transient": transient,
            "needs_human": needs_human,
            "checked": (len(confirmed) + len(redirected) + len(transient)
                        + len(needs_human))}
