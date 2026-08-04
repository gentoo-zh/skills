from __future__ import annotations

import re
import subprocess
from pathlib import Path

# This threshold selects large files for an advisory remote-size comparison. It is not a
# Gentoo policy limit and a matching size does not establish artifact provenance.
_LARGE_DISTFILE = 50 * 1024 * 1024  # 50 MiB; the bigger the file the more it matters
_URL_SCHEMES = ("http://", "https://", "ftp://", "mirror://")


def run_manifest(ebuild: Path, cwd: Path | None = None,
                 distdir: Path | None = None,
                 runner=subprocess.run) -> dict:
    args = ["pkgdev", "manifest", "--force", str(ebuild)]
    if distdir is not None:
        args[2:2] = ["--distdir", str(Path(distdir))]
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    return {"ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}


def parse_manifest_dist(manifest_text: str) -> list[dict]:
    """DIST lines from a Manifest -> [{'name', 'size'}]. Format:
    `DIST <name> <size> BLAKE2B <h> SHA512 <h>`."""
    out = []
    for line in (manifest_text or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == "DIST" and parts[2].isdigit():
            out.append({"name": parts[1], "size": int(parts[2])})
    return out


def _pv_subs(ebuild_name: str) -> dict:
    """Derive ${P}/${PN}/${PV}/${PVR}/${PF} from a `<pn>-<pvr>.ebuild` filename."""
    stem = re.sub(r"\.ebuild$", "", ebuild_name)
    m = re.match(r"(?P<pn>.+?)-(?P<pvr>\d[^-]*(?:-r\d+)?)$", stem)
    if not m:
        return {}
    pn, pvr = m.group("pn"), m.group("pvr")
    pv = re.sub(r"-r\d+$", "", pvr)
    return {"P": f"{pn}-{pv}", "PF": f"{pn}-{pvr}", "PN": pn, "PV": pv, "PVR": pvr}


def _expand(tok: str, subs: dict) -> str:
    # longest key first: a bare `$P` must not eat the prefix of `$PF`/`$PN`/`$PV`/`$PVR`.
    for k, v in sorted(subs.items(), key=lambda kv: len(kv[0]), reverse=True):
        tok = tok.replace("${%s}" % k, v).replace("$%s" % k, v)
    return tok


def extract_src_uri_map(ebuild_text: str, subs: dict | None = None) -> dict:
    """filename -> URL from SRC_URI, multiline-aware (NOT the line-oriented ebuild parser,
    which breaks on multi-line quoted SRC_URI). Honors `url -> rename`, skips `use? ( )` /
    `|| ( )` group tokens, and does simple ${P}/${PV}/... expansion from `subs`. URLs that
    still contain an unexpanded ${...} are dropped (cannot resolve offline)."""
    subs = subs or {}
    values = [m.group(2) for m in
              re.finditer(r"\bSRC_URI\s*\+?=\s*([\"'])(.*?)\1", ebuild_text or "", re.DOTALL)]
    tokens = " ".join(values).split()
    out: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.endswith("?") or tok in ("(", ")", "||"):
            i += 1
            continue
        if tok.startswith(_URL_SCHEMES):
            url = _expand(tok, subs)
            if i + 2 < len(tokens) and tokens[i + 1] == "->":
                name = _expand(tokens[i + 2], subs)
                i += 3
            else:
                name = url.rsplit("/", 1)[-1]
                i += 1
            if "${" not in url and "${" not in name:
                out[name] = url
        else:
            i += 1
    return out


def remote_size(url: str, runner=subprocess.run) -> int | None:
    """Upstream byte size via a HEAD Content-Length, falling back to a ranged GET's
    Content-Range total. None = inconclusive (never treat as a mismatch)."""
    head = runner(["curl", "-sIL", "--max-time", "30", "-o", "/dev/null",
                   "-w", "%header{content-length}", url],
                  capture_output=True, text=True)
    val = (head.stdout or "").strip().splitlines()[-1:] or [""]
    if val[0].isdigit():
        return int(val[0])
    rng = runner(["curl", "-sL", "-r", "0-0", "--max-time", "30", "-o", "/dev/null",
                  "-w", "%header{content-range}", url], capture_output=True, text=True)
    m = re.search(r"/(\d+)\s*$", (rng.stdout or "").strip())
    return int(m.group(1)) if m else None


def verify_manifest_sizes(manifest_text: str, src_uri_map: dict,
                          threshold: int = _LARGE_DISTFILE,
                          runner=subprocess.run) -> dict:
    """Compare each large DIST entry's recorded size to the upstream URL's size. A
    mismatch (remote known and != local) means a truncated/rotated distfile that would
    pass local install but fail CI VERIFY. Small files and unresolved URLs are skipped."""
    mismatches, checked, unresolved, inconclusive, skipped = [], [], [], [], []
    for d in parse_manifest_dist(manifest_text):
        if d["size"] < threshold:
            skipped.append({**d, "reason": "below-advisory-threshold"})
            continue
        url = src_uri_map.get(d["name"])
        if not url:
            unresolved.append({**d, "reason": "no-resolved-src-uri"})
            continue
        rsize = remote_size(url, runner=runner)
        checked.append({"name": d["name"], "local": d["size"], "remote": rsize})
        if rsize is None:
            inconclusive.append({"name": d["name"], "url": url,
                                 "reason": "remote-size-unavailable"})
        elif rsize != d["size"]:
            mismatches.append({"name": d["name"], "local": d["size"],
                               "remote": rsize, "url": url})
    complete = not unresolved and not inconclusive
    return {"ok": complete and not mismatches, "complete": complete,
            "truncated": False, "mismatches": mismatches, "checked": checked,
            "unresolved": unresolved, "inconclusive": inconclusive,
            "skipped": skipped,
            "remediation": ("rm the distfile + its stale DIST line, "
                            "`curl -L -C - --retry 3 <url>`, then re-run `gzh manifest`")
            if mismatches else None}
