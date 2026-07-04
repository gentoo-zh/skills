from __future__ import annotations

import re

_GITHUB_RE = re.compile(r'github\.com/([^/]+)/([^/)\."\'\s]+)')


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
    if "gitlab.com" in text or "codeberg.org" in text or ".git" in text:
        return "git", {"source": "git", "use_max_tag": True}
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
