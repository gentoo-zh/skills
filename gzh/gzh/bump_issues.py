from __future__ import annotations

import json
import re
import subprocess

_TITLE_RE = re.compile(r'^\[nvchecker\]\s+(\S+)\s+can be bump to\s+(\S+)$')
_OLDVER_RE = re.compile(r'oldver:\s*(\S+)', re.IGNORECASE)
_CC_RE = re.compile(r'CC:\s*@?(\S+)', re.IGNORECASE)


def parse_title(title: str) -> tuple[str, str] | None:
    m = _TITLE_RE.match((title or "").strip())
    return (m.group(1), m.group(2)) if m else None


def parse_body(body: str) -> dict:
    body = body or ""
    m_old = _OLDVER_RE.search(body)
    m_cc = _CC_RE.search(body)
    return {"oldver": m_old.group(1) if m_old else None,
            "maintainer": m_cc.group(1) if m_cc else None}


def graphql_to_queue(nodes: list, with_comments: bool = True) -> tuple[list[dict], int]:
    queue: list[dict] = []
    skipped = 0
    for n in nodes or []:
        parsed = parse_title(n.get("title", ""))
        if parsed is None:
            skipped += 1
            continue
        cat_pkg, target = parsed
        body = parse_body(n.get("body", "") or "")
        item = {
            "issue": n.get("number"),
            "cat_pkg": cat_pkg,
            "target_version": target,
            "oldver": body["oldver"],
            "maintainer": body["maintainer"],
            "title": n.get("title", ""),
            "url": n.get("url"),
            "state": (n.get("state") or "").lower() or None,
        }
        if with_comments:
            cnodes = ((n.get("comments") or {}).get("nodes")) or []
            item["comments"] = [
                {"author": (c.get("author") or {}).get("login"),
                 "body": c.get("body"),
                 "created_at": c.get("createdAt")}
                for c in cnodes[:50]
            ]
            item["comments_truncated"] = len(cnodes) > 50
        else:
            item["comments"] = []
            item["comments_truncated"] = False
        queue.append(item)
    return queue, skipped


def apply_filters(queue: list, maintainer: str | None = None,
                  pkg: str | None = None) -> list[dict]:
    out = queue
    if maintainer:
        out = [x for x in out if x.get("maintainer") == maintainer]
    if pkg:
        out = [x for x in out if x.get("cat_pkg") == pkg]
    return out


_STATE_MAP = {"open": "OPEN", "closed": "CLOSED"}


def build_query(owner: str, name: str, state: str | None,
                limit: int, with_comments: bool) -> str:
    args = ['labels:["nvchecker"]', f"first:{limit}"]
    if state:
        args.append(f"states:[{state}]")
    comments_block = " comments(first:50){nodes{author{login} body createdAt}}" if with_comments else ""
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issues({', '.join(args)}) {{\n"
        f"      nodes {{ number title body state url author {{login}}{comments_block} }}\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _check_gh_auth(runner) -> bool:
    return runner(["gh", "auth", "status"], capture_output=True, text=True).returncode == 0


def run_bump_issues(repo: str = "Gentoo-zh/gentoo-zh", state: str = "open",
                    maintainer: str | None = None, pkg: str | None = None,
                    with_comments: bool = True, limit: int = 100,
                    runner=None) -> dict:
    if runner is None:
        runner = subprocess.run
    limit = min(int(limit), 100)
    if not _check_gh_auth(runner):
        return {"ok": False, "exit_code": 2,
                "error": "gh not authenticated; run `gh auth login` first"}
    owner, _, name = repo.partition("/")
    if not name:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid --repo: {repo!r} (expect owner/name)"}
    gstate = _STATE_MAP.get(state)  # None for "all"
    query = build_query(owner, name, gstate, limit, with_comments)
    proc = runner(["gh", "api", "graphql", "-f", f"query={query}"],
                  capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "exit_code": 1,
                "error": "gh graphql call failed", "stderr": proc.stderr}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "exit_code": 1,
                "error": "invalid JSON from gh", "stdout": proc.stdout}
    if data.get("errors"):
        return {"ok": False, "exit_code": 1, "error": str(data["errors"])}
    nodes = (((data.get("data") or {}).get("repository") or {})
             .get("issues") or {}).get("nodes") or []
    queue, skipped = graphql_to_queue(nodes, with_comments=with_comments)
    queue = apply_filters(queue, maintainer=maintainer, pkg=pkg)
    return {"ok": True, "results": queue, "skipped": skipped, "exit_code": 0}
