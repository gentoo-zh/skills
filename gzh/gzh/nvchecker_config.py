from __future__ import annotations

import re
import tomllib
from pathlib import Path

import tomlkit

_TABLE_RE = re.compile(r'^\["([^"]+)"\]\s*$')


def _load(path: Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def get_entry(overlay_toml: Path, cat_pkg: str) -> dict | None:
    return _load(overlay_toml).get(cat_pkg)


def sort_overlay_toml(text: str) -> str:
    """Sort package blocks alphabetically by cat/pkg; comments preceding a
    block travel with it. __config__ header stays on top. Commented-out
    tables (#["..."]) are treated as comments (travel with next block)."""
    lines = text.split("\n")
    table_idx = [i for i, l in enumerate(lines) if _TABLE_RE.match(l.strip())]
    if not table_idx:
        return text
    header = lines[:table_idx[0]]
    starts = []
    for j, ti in enumerate(table_idx):
        bs = ti
        lower = (table_idx[j-1] + 1) if j > 0 else len(header)
        while bs - 1 >= lower:
            prev = lines[bs-1].strip()
            if prev == "" or prev.startswith("#"):
                bs -= 1
            else:
                break
        starts.append(bs)
    blocks = []
    for j, ti in enumerate(table_idx):
        end = starts[j+1] if j+1 < len(starts) else len(lines)
        key = _TABLE_RE.match(lines[ti].strip()).group(1)
        blocks.append((key, lines[starts[j]:end]))
    blocks.sort(key=lambda b: b[0])
    out = list(header)
    while out and out[-1].strip() == "":
        out.pop()
    for _, blk in blocks:
        b = blk.copy()
        while b and b[0].strip() == "":
            b.pop(0)
        while b and b[-1].strip() == "":
            b.pop()
        out.append("")  # exactly one blank line between blocks
        out.extend(b)
    return "\n".join(out)


def set_entry(overlay_toml: Path, cat_pkg: str, entry: dict) -> None:
    # tomlkit preserves comments/formatting (tomli-w would drop them).
    doc = tomlkit.parse(Path(overlay_toml).read_text(encoding="utf-8"))
    if cat_pkg in doc:
        del doc[cat_pkg]
    doc[cat_pkg] = entry
    text = sort_overlay_toml(tomlkit.dumps(doc))
    Path(overlay_toml).write_text(text, encoding="utf-8")
