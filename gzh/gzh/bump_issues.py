from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_TITLE_RE = re.compile(r'^\[nvchecker\]\s+(\S+)\s+can be bump to\s+(\S+)$')
_OLDVER_RE = re.compile(r'oldver:\s*(\S+)', re.IGNORECASE)
_CC_RE = re.compile(r'CC:\s*@?(\S+)', re.IGNORECASE)
_REPO_PART_RE = re.compile(r"[A-Za-z0-9_.-]+")


def split_repo(repo: str) -> tuple[str, str]:
    owner, separator, name = repo.partition("/")
    if (not separator or not name or "/" in name
            or not _REPO_PART_RE.fullmatch(owner)
            or not _REPO_PART_RE.fullmatch(name)):
        raise ValueError(f"invalid repository: {repo!r} (expect owner/name)")
    return owner, name


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
        raw_body = n.get("body", "") or ""
        body = parse_body(raw_body)
        item = {
            "issue": n.get("number"),
            "cat_pkg": cat_pkg,
            "target_version": target,
            "oldver": body["oldver"],
            "maintainer": body["maintainer"],
            "title": n.get("title", ""),
            "body": raw_body,
            "url": n.get("url"),
            "state": (n.get("state") or "").lower() or None,
            "updated_at": n.get("updatedAt"),
        }
        if with_comments:
            comments = n.get("comments") or {}
            cnodes = comments.get("nodes") or []
            item["comments"] = [
                {"author": (c.get("author") or {}).get("login"),
                 "body": c.get("body"),
                 "created_at": c.get("createdAt")}
                for c in cnodes
            ]
            total = comments.get("totalCount", len(cnodes))
            item["comments_truncated"] = total > len(cnodes)
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
                limit: int, with_comments: bool,
                after: str | None = None) -> str:
    args = ['labels:["nvchecker"]', f"first:{min(int(limit), 100)}"]
    if state:
        args.append(f"states:[{state}]")
    if after:
        args.append(f"after:{json.dumps(after)}")
    comments_block = (
        " comments(first:100){totalCount pageInfo{hasNextPage endCursor} "
        "nodes{author{login} body createdAt}}" if with_comments else "")
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issues({', '.join(args)}) {{\n"
        "      totalCount\n"
        "      pageInfo { hasNextPage endCursor }\n"
        f"      nodes {{ number title body state url updatedAt author {{login}}{comments_block} }}\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def build_comments_query(owner: str, name: str, issue: int,
                         after: str) -> str:
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issue(number:{issue}) {{\n"
        f"      comments(first:100,after:{json.dumps(after)}) {{\n"
        "        totalCount pageInfo { hasNextPage endCursor }\n"
        "        nodes { author { login } body createdAt }\n"
        "      }\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _check_gh_auth(runner) -> bool:
    try:
        return runner(["gh", "auth", "status"], capture_output=True, text=True).returncode == 0
    except FileNotFoundError:
        return False


def _graphql(query: str, runner) -> tuple[dict | None, dict | None]:
    proc = runner(["gh", "api", "graphql", "-f", f"query={query}"],
                  capture_output=True, text=True)
    if proc.returncode != 0:
        return None, {"ok": False, "exit_code": 1,
                      "error": "gh graphql call failed", "stderr": proc.stderr}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None, {"ok": False, "exit_code": 1,
                      "error": "invalid JSON from gh", "stdout": proc.stdout}
    if data.get("errors"):
        return None, {"ok": False, "exit_code": 1,
                      "error": str(data["errors"])}
    return data, None


def get_issue_updated_at(repo: str, issue: int, runner=None) -> str:
    """Read the current GitHub issue revision immediately before triage writes."""
    if runner is None:
        runner = subprocess.run
    owner, name = split_repo(repo)
    if int(issue) < 1:
        raise ValueError(f"invalid issue number: {issue}")
    proc = runner(
        ["gh", "api", f"repos/{owner}/{name}/issues/{int(issue)}",
         "--jq", ".updated_at"],
        capture_output=True, text=True)
    updated_at = (proc.stdout or "").strip()
    if proc.returncode != 0 or not updated_at:
        raise RuntimeError(
            proc.stderr.strip() or f"cannot read current revision for issue {issue}")
    return updated_at


def _fetch_remaining_comments(nodes: list[dict], owner: str, name: str,
                              runner) -> dict | None:
    for node in nodes:
        comments = node.get("comments") or {}
        page_info = comments.get("pageInfo") or {}
        seen_cursors = set()
        while page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
            if not cursor or cursor in seen_cursors:
                return {"ok": False, "exit_code": 1,
                        "error": f"invalid comment cursor for issue {node.get('number')}"}
            seen_cursors.add(cursor)
            query = build_comments_query(owner, name, node["number"], cursor)
            data, error = _graphql(query, runner)
            if error:
                return error
            page = (((data.get("data") or {}).get("repository") or {})
                    .get("issue") or {}).get("comments") or {}
            comments.setdefault("nodes", []).extend(page.get("nodes") or [])
            comments["totalCount"] = page.get(
                "totalCount", comments.get("totalCount", len(comments["nodes"])))
            page_info = page.get("pageInfo") or {}
            comments["pageInfo"] = page_info
    return None


def run_bump_issues(repo: str = "gentoo-zh/overlay", state: str = "open",
                    maintainer: str | None = None, pkg: str | None = None,
                    with_comments: bool = True, limit: int = 100,
                    runner=None) -> dict:
    if runner is None:
        runner = subprocess.run
    limit = min(int(limit), 1000)
    if not _check_gh_auth(runner):
        return {"ok": False, "exit_code": 2,
                "error": "gh not authenticated (or not installed); run `gh auth login` first"}
    try:
        owner, name = split_repo(repo)
    except ValueError:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid --repo: {repo!r} (expect owner/name)"}
    gstate = _STATE_MAP.get(state)  # None for "all"
    nodes = []
    cursor = None
    seen_cursors = set()
    total_count = 0
    more_available = False
    while len(nodes) < limit:
        query = build_query(owner, name, gstate, min(100, limit - len(nodes)),
                            with_comments, after=cursor)
        data, error = _graphql(query, runner)
        if error:
            return error
        issues = (((data.get("data") or {}).get("repository") or {})
                  .get("issues") or {})
        page_nodes = issues.get("nodes") or []
        nodes.extend(page_nodes)
        reported_total = issues.get("totalCount")
        if isinstance(reported_total, int):
            total_count = max(total_count, reported_total)
        page_info = issues.get("pageInfo") or {}
        more_available = bool(page_info.get("hasNextPage"))
        if not more_available:
            break
        cursor = page_info.get("endCursor")
        if not cursor or cursor in seen_cursors:
            return {"ok": False, "exit_code": 1,
                    "error": "invalid issue pagination cursor"}
        seen_cursors.add(cursor)
    if with_comments:
        error = _fetch_remaining_comments(nodes, owner, name, runner)
        if error:
            return error
    queue, skipped = graphql_to_queue(nodes, with_comments=with_comments)
    queue = apply_filters(queue, maintainer=maintainer, pkg=pkg)
    fetched_count = len(nodes)
    total_count = max(total_count, fetched_count)
    return {"ok": True, "results": queue, "skipped": skipped,
            "total_count": total_count, "fetched_count": fetched_count,
            "selected_count": len(queue),
            "truncated": more_available or total_count > fetched_count,
            "exit_code": 0}


def write_output(payload: dict, out_dir: Path, timestamp: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    for suffix in range(10000):
        discriminator = "" if suffix == 0 else f"-{suffix}"
        path = out_dir / f"bump-issues-{timestamp}{discriminator}.json"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(content)
            return path
        except FileExistsError:
            continue
    raise RuntimeError("cannot allocate a unique bump issue snapshot")
