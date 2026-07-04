from __future__ import annotations

import tomllib
from pathlib import Path

import tomlkit


def _load(path: Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def get_entry(overlay_toml: Path, cat_pkg: str) -> dict | None:
    return _load(overlay_toml).get(cat_pkg)


def set_entry(overlay_toml: Path, cat_pkg: str, entry: dict) -> None:
    # tomlkit preserves comments/formatting (tomli-w would drop them).
    doc = tomlkit.parse(Path(overlay_toml).read_text(encoding="utf-8"))
    if cat_pkg in doc:
        del doc[cat_pkg]
    doc[cat_pkg] = entry
    Path(overlay_toml).write_text(tomlkit.dumps(doc), encoding="utf-8")
