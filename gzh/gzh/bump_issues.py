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
