from __future__ import annotations

import difflib
import shutil
from pathlib import Path


def _ebuilds(pkg_dir: Path, pn: str) -> list[Path]:
    return sorted(pkg_dir.glob(f"{pn}-*.ebuild"))


def highest_ebuild(pkg_dir: Path, pn: str) -> Path | None:
    ebs = _ebuilds(pkg_dir, pn)
    return ebs[-1] if ebs else None


def bump_scaffold(pkg_dir: Path, pn: str, new_pv: str) -> Path:
    src = highest_ebuild(pkg_dir, pn)
    if src is None:
        raise FileNotFoundError(f"no existing ebuild for {pn} in {pkg_dir}")
    dst = pkg_dir / f"{pn}-{new_pv}.ebuild"
    if dst.exists():
        raise FileExistsError(f"target already exists: {dst}")
    shutil.copy2(src, dst)
    return dst


def diff_ebuild(old: Path, new: Path) -> str:
    return "".join(difflib.unified_diff(
        old.read_text().splitlines(keepends=True),
        new.read_text().splitlines(keepends=True),
        fromfile=str(old), tofile=str(new)))
