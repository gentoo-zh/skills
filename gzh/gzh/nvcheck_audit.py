from __future__ import annotations

import re
import tomllib
from pathlib import Path

from gzh.ebuild_parser import parse_ebuild
from gzh.nvchecker_config import set_entry
from gzh.repo import find_overlay_root

_GITHUB_RE = re.compile(r"github\.com/([^/]+)/([^/)\"'\s]+)")


def _clean_repo(repo: str) -> str:
    return repo.removesuffix(".git").rstrip("/")


def infer_source(parsed: dict, pn: str) -> tuple[str, dict | None]:
    homepage = parsed.get("HOMEPAGE", "") or ""
    src_uri = parsed.get("SRC_URI", "") or ""
    text = f"{homepage} {src_uri}"
    inherit = parsed.get("inherit", []) or []

    m = _GITHUB_RE.search(text)
    if m:
        org, repo = m.group(1), _clean_repo(m.group(2))
        return "github", {"source": "github", "github": f"{org}/{repo}",
                          "use_latest_release": True}
    if "pypi.org" in text or "files.pythonhosted.org" in text or "pypi" in inherit:
        return "pypi", {"source": "pypi", "pypi": pn}
    # git source is not auto-inferred: HOMEPAGE/SRC_URI patterns (gitlab/codeberg/.git)
    # don't reliably yield a clone-able src; leave as unknown for manual setup.
    return "unknown", None


SYSTEM_CATEGORIES = {"acct-group", "acct-user", "virtual"}


def _is_system(cat_pkg: str) -> bool:
    cat = cat_pkg.split("/", 1)[0]
    return cat in SYSTEM_CATEGORIES


def audit(configured: set, actual: set, filter_system: bool = True) -> tuple[list[str], list[str]]:
    stale = sorted(configured - actual)
    missing = sorted(actual - configured)
    if filter_system:
        missing = [p for p in missing if not _is_system(p)]
    return stale, missing


def _load_configured(overlay_toml: Path) -> set[str]:
    data = tomllib.loads(Path(overlay_toml).read_text(encoding="utf-8"))
    return {k for k in data if "/" in k and k != "__config__"}


def _enumerate_actual(root: Path) -> set[str]:
    out: set[str] = set()
    for cat_d in Path(root).iterdir():
        if not cat_d.is_dir() or cat_d.name.startswith("."):
            continue
        if cat_d.name in ("metadata", "profiles"):
            continue
        for pkg_d in cat_d.iterdir():
            if pkg_d.is_dir() and any(pkg_d.glob("*.ebuild")):
                out.add(f"{cat_d.name}/{pkg_d.name}")
    return out


def run_audit(apply: bool = False, filter_system: bool = True,
              overlay_root: Path | None = None, set_entry_fn=set_entry) -> dict:
    root = Path(overlay_root) if overlay_root else find_overlay_root()
    overlay_toml = root / ".github" / "workflows" / "overlay.toml"
    try:
        configured = _load_configured(overlay_toml)
        actual = _enumerate_actual(root)
    except (OSError, tomllib.TOMLDecodeError) as e:
        return {"ok": False, "error": f"failed to read {overlay_toml.name}: {e}",
                "stale": [], "missing": [], "skipped_unknown": []}
    stale, missing = audit(configured, actual, filter_system=filter_system)

    out_missing: list[dict] = []
    skipped_unknown: list[str] = []
    skipped_live: list[str] = []
    for cat_pkg in missing:
        cat, pn = cat_pkg.split("/", 1)
        pkg_dir = root / cat / pn
        ebs = sorted(pkg_dir.glob(f"{pn}-*.ebuild"))
        if not ebs:
            continue
        pvs = [eb.name[len(pn) + 1:].removesuffix(".ebuild") for eb in ebs]
        if pvs and all(pv == "9999" or pv.startswith("9999") for pv in pvs):
            skipped_live.append(cat_pkg)
            continue
        try:
            parsed = parse_ebuild(ebs[-1])
            source, entry = infer_source(parsed, pn)
        except (OSError, ValueError, UnicodeDecodeError) as e:
            skipped_unknown.append(cat_pkg)
            continue
        if source == "unknown" or entry is None:
            skipped_unknown.append(cat_pkg)
            continue
        applied = False
        if apply:
            set_entry_fn(overlay_toml, cat_pkg, entry)
            applied = True
        out_missing.append({"cat_pkg": cat_pkg, "source": source,
                            "entry": entry, "applied": applied})
    return {"ok": True, "stale": stale, "missing": out_missing,
            "skipped_unknown": skipped_unknown, "skipped_live": skipped_live}
