from __future__ import annotations

from functools import cmp_to_key
from pathlib import Path

from portage.versions import vercmp

from gzh.ebuild_parser import is_live, pv_from_name
from gzh.repo import find_overlay_root


def list_ebuilds(pkg_dir: Path, pn: str) -> list[Path]:
    return list(Path(pkg_dir).glob(f"{pn}-*.ebuild"))


def drop_candidates(ebuilds: list[Path], pn: str, keep: int = 2,
                    vercmp=vercmp) -> tuple[list[Path], list[Path]]:
    liveup: list[Path] = []
    rest: list[tuple[str, Path]] = []
    for eb in ebuilds:
        pv = pv_from_name(eb.name)
        if is_live(pv):
            liveup.append(eb)
        else:
            rest.append((pv, eb))
    rest.sort(key=cmp_to_key(lambda a, b: vercmp(a[0], b[0])))  # ascending
    rest_ebs = [eb for _, eb in rest]
    kept = rest_ebs[-keep:] + liveup    # newest N (ascending) + liveup
    dropped = rest_ebs[:-keep]          # older ones (ascending)
    return dropped, kept


def _enumerate_pkgs(root: Path, target: str) -> list[str]:
    root = Path(root)
    if target != "all":
        return [target]
    out: list[str] = []
    for cat_dir in root.iterdir():
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        for pkg_d in cat_dir.iterdir():
            if pkg_d.is_dir() and any(pkg_d.glob("*.ebuild")):
                out.append(f"{cat_dir.name}/{pkg_d.name}")
    return sorted(out)


def run_drop_old(target: str, keep: int = 2, apply: bool = False,
                 overlay_root: Path | None = None) -> dict:
    if apply:
        raise ValueError(
            "drop-old --apply is disabled: version retention requires package-history "
            "and reverse-dependency review")
    root = Path(overlay_root) if overlay_root else find_overlay_root()
    results = []
    for cat_pkg in _enumerate_pkgs(root, target):
        cat, pn = cat_pkg.split("/", 1)
        pkg_dir = root / cat / pn
        if not pkg_dir.is_dir():
            continue
        ebs = list_ebuilds(pkg_dir, pn)
        dropped, kept = drop_candidates(ebs, pn, keep=keep)
        entry = {"cat_pkg": cat_pkg,
                 "dropped": [p.name for p in dropped],
                 "kept": [p.name for p in kept]}
        results.append(entry)
    return {"ok": True, "results": results}
