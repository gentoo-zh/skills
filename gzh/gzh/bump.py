from __future__ import annotations

import difflib
import re
import shutil
from datetime import date
from functools import cmp_to_key
from pathlib import Path

from portage.versions import vercmp, ververify

from gzh.ebuild_parser import is_live, pv_from_name


_CATEGORY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_.-]*")
_PACKAGE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+_-]*")


def resolve_package_directory(root: Path, cat_pkg: str) -> tuple[str, str, Path]:
    parts = cat_pkg.split("/")
    if (len(parts) != 2 or not _CATEGORY_RE.fullmatch(parts[0])
            or not _PACKAGE_RE.fullmatch(parts[1])):
        raise ValueError(f"invalid category/package: {cat_pkg}")
    root = Path(root).resolve()
    category, package = parts
    pkg_dir = (root / category / package).resolve()
    if root not in pkg_dir.parents or not pkg_dir.is_dir():
        raise ValueError(f"package directory does not exist in the overlay: {cat_pkg}")
    return category, package, pkg_dir


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


# Same shape pkgcheck's header check matches, so a refreshed line still parses there.
_COPYRIGHT_RE = re.compile(
    r"^# Copyright (?:(?P<begin>\d{4})-)?(?P<end>\d{4}) (?P<holder>.+)$")


def refresh_copyright_year(path: Path, year: int | None = None) -> bool:
    """Move an ebuild's copyright end year to `year`, keeping the original start.

    pkgcheck reports EbuildIncorrectCopyright for any changed file whose end year is
    not the current one, but only as a warning, so neither the pre-PR gate (--exit
    error) nor the overlay's CI (--exit=NonexistentDeps) stops on it. Scaffolding
    copies the file verbatim, so without this every bump would carry the old year.
    Returns whether the file changed.
    """
    year = year or date.today().year
    lines = path.read_text().splitlines(keepends=True)
    if not lines:
        return False
    m = _COPYRIGHT_RE.match(lines[0].rstrip("\n"))
    if m is None or m.group("end") == str(year):
        return False
    begin = m.group("begin") or m.group("end")
    span = str(year) if begin == str(year) else f"{begin}-{year}"
    lines[0] = f"# Copyright {span} {m.group('holder')}\n"
    path.write_text("".join(lines))
    return True


def bump_scaffold(pkg_dir: Path, pn: str, new_pv: str) -> Path:
    if not _PACKAGE_RE.fullmatch(pn) or pkg_dir.name != pn:
        raise ValueError(f"invalid package name for directory: {pn}")
    if not ververify(new_pv) or is_live(new_pv):
        raise ValueError(f"invalid released Gentoo version: {new_pv}")
    src = highest_ebuild(pkg_dir, pn)
    if src is None:
        raise FileNotFoundError(
            f"no released ebuild for {pn} in {pkg_dir} "
            "(live-only or empty); escalate instead of scaffolding from 9999")
    dst = pkg_dir / f"{pn}-{new_pv}.ebuild"
    if dst.exists():
        raise FileExistsError(f"target already exists: {dst}")
    shutil.copy2(src, dst)
    refresh_copyright_year(dst)
    return dst


def diff_ebuild(old: Path, new: Path) -> str:
    return "".join(difflib.unified_diff(
        old.read_text().splitlines(keepends=True),
        new.read_text().splitlines(keepends=True),
        fromfile=str(old), tofile=str(new)))
