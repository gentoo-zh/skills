from __future__ import annotations

import difflib
import shutil
from functools import cmp_to_key
from pathlib import Path

from portage.versions import vercmp

from gzh.ebuild_parser import is_live, pv_from_name


def _ebuilds(pkg_dir: Path, pn: str) -> list[Path]:
    """Released ebuilds, ascending by vercmp.

    Live (9999*) ebuilds are excluded: they carry EGIT_REPO_URI instead of
    SRC_URI and have no KEYWORDS, so they are not a usable bump template.
    Sorting by filename would also rank 1.9 above 1.10.
    """
    rest = []
    for path in pkg_dir.glob(f"{pn}-*.ebuild"):
        pv = pv_from_name(path.name)
        if not is_live(pv):
            rest.append((pv, path))
    rest.sort(key=cmp_to_key(lambda a, b: vercmp(a[0], b[0])))
    return [path for _, path in rest]


def highest_ebuild(pkg_dir: Path, pn: str) -> Path | None:
    ebs = _ebuilds(pkg_dir, pn)
    return ebs[-1] if ebs else None


def bump_scaffold(pkg_dir: Path, pn: str, new_pv: str) -> Path:
    src = highest_ebuild(pkg_dir, pn)
    if src is None:
        raise FileNotFoundError(
            f"no released ebuild for {pn} in {pkg_dir} "
            "(live-only or empty); escalate instead of scaffolding from 9999")
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
