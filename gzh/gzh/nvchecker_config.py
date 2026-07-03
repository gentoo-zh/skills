from __future__ import annotations

import tomllib
import tomli_w
from pathlib import Path


def _load(path: Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def get_entry(overlay_toml: Path, cat_pkg: str) -> dict | None:
    return _load(overlay_toml).get(cat_pkg)


def set_entry(overlay_toml: Path, cat_pkg: str, entry: dict) -> None:
    data = _load(overlay_toml)
    data[cat_pkg] = entry
    Path(overlay_toml).write_text(tomli_w.dumps(data), encoding="utf-8")
