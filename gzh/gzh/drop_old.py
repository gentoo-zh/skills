from __future__ import annotations

from functools import cmp_to_key
from pathlib import Path

from portage.versions import vercmp


def list_ebuilds(pkg_dir: Path, pn: str) -> list[Path]:
    return list(Path(pkg_dir).glob(f"{pn}-*.ebuild"))


def _pv_from_name(name: str, pn: str) -> str:
    stem = name.removesuffix(".ebuild")
    return stem[len(pn) + 1:]


def _is_liveup(pv: str) -> bool:
    return pv == "9999" or pv.startswith("9999")


def drop_candidates(ebuilds: list[Path], pn: str, keep: int = 2,
                    vercmp=vercmp) -> tuple[list[Path], list[Path]]:
    liveup: list[Path] = []
    rest: list[tuple[str, Path]] = []
    for eb in ebuilds:
        pv = _pv_from_name(eb.name, pn)
        if _is_liveup(pv):
            liveup.append(eb)
        else:
            rest.append((pv, eb))
    rest.sort(key=cmp_to_key(lambda a, b: vercmp(a[0], b[0])))  # ascending
    rest_ebs = [eb for _, eb in rest]
    kept = rest_ebs[-keep:] + liveup    # newest N (ascending) + liveup
    dropped = rest_ebs[:-keep]          # older ones (ascending)
    return dropped, kept
