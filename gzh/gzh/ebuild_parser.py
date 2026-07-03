from __future__ import annotations

import re
from pathlib import Path

_VAR_RE = re.compile(r'^([A-Z_][A-Z0-9_]*)=(.*)$', re.MULTILINE)
_QUOTED_RE = re.compile(r'^"(.*)"$')


def _pv_from_name(name: str) -> str:
    stem = name.removesuffix(".ebuild")
    m = re.match(r'^(.+)-(\d.*)$', stem)
    return m.group(2) if m else ""


def _strip(raw: str) -> str:
    raw = raw.strip()
    m = _QUOTED_RE.match(raw)
    if m:
        return m.group(1)
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def parse_ebuild(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    text = re.sub(r"\\\n", "", text)  # join line continuations
    result: dict[str, str] = {}
    for m in _VAR_RE.finditer(text):
        result[m.group(1)] = _strip(m.group(2))
    result["PV"] = _pv_from_name(Path(path).name)
    return result
