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
