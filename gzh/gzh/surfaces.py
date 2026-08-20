"""Classify which ebuild surfaces a bump touched.

The routing rule is to load a reference only for a surface the release actually
changed. That decision needs a deterministic input, so this module compares two
ebuilds after normalizing their versions and reports per surface.

It classifies the ebuild text only. Upstream may change a dependency without the
ebuild changing, so an unchanged surface here is a precondition for skipping its
review, never the whole evidence.
"""

from __future__ import annotations

import re
from pathlib import Path

from gzh.ebuild_parser import pv_from_name

MAX_EBUILD_BYTES = 512 * 1024
_VERSION_PLACEHOLDER = "\x00GZH_VERSION\x00"
_VAR_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)\+?=", re.MULTILINE)
_FUNCTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*\(\)\s*\{", re.MULTILINE)

SURFACE_VARIABLES = {
    "dependencies": (
        "DEPEND", "RDEPEND", "BDEPEND", "IDEPEND", "PDEPEND", "REQUIRED_USE"),
    "use": ("IUSE",),
    "licensing": ("LICENSE", "RESTRICT", "PROPERTIES"),
    "artifacts": ("SRC_URI", "S", "MY_P", "MY_PV", "MY_PN"),
    "keywords": ("KEYWORDS",),
    "eclasses": ("EAPI", "inherit"),
    "metadata": ("DESCRIPTION", "HOMEPAGE", "SLOT"),
    "patches": ("PATCHES",),
    "prebuilt_qa": ("QA_PREBUILT", "QA_SONAME", "QA_FLAGS_IGNORED"),
}

HEADER_BLOCK = "header"
INSTALL_PHASES = ("src_install", "pkg_preinst", "pkg_postinst", "pkg_setup")


class SurfaceError(ValueError):
    pass


def _read(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise SurfaceError(f"not a regular ebuild file: {path}")
    if path.stat().st_size > MAX_EBUILD_BYTES:
        raise SurfaceError(f"ebuild exceeds {MAX_EBUILD_BYTES} bytes: {path}")
    return path.read_text(encoding="utf-8")


def _normalize(text: str, version: str) -> str:
    """Replace the literal version so a pure rename compares equal."""
    if version:
        text = text.replace(version, _VERSION_PLACEHOLDER)
        base = version.split("-r", 1)[0]
        if base != version:
            text = text.replace(base, _VERSION_PLACEHOLDER)
    return text


def _blocks(text: str) -> dict[str, str]:
    """Return each top-level assignment and function body, keyed by name.

    An assignment runs to the next assignment, function, or end of file, so a
    multi-line value is captured whole without interpreting shell syntax.
    """
    marks: list[tuple[int, str]] = []
    for match in _VAR_RE.finditer(text):
        marks.append((match.start(), match.group(1)))
    for match in _FUNCTION_RE.finditer(text):
        marks.append((match.start(), match.group(1)))
    for match in re.finditer(r"^inherit\s", text, re.MULTILINE):
        marks.append((match.start(), "inherit"))
    marks.sort()
    blocks: dict[str, str] = {}
    # Everything before the first assignment is the header. Without it a
    # copyright-year refresh would differ textually yet match no block, and the
    # report would read as "nothing changed".
    blocks[HEADER_BLOCK] = text[:marks[0][0]] if marks else text
    for index, (start, name) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(text)
        blocks[name] = blocks.get(name, "") + text[start:end]
    return blocks


def _referenced(text: str, name: str) -> bool:
    return bool(re.search(r"\$\{?" + re.escape(name) + r"\}?\b", text))


def _surface_state(old: dict[str, str], new: dict[str, str],
                   names: tuple[str, ...], differing_blocks: set[str]) -> dict:
    """A surface changed when its own text differs or it reads a changed name.

    A package that keeps its artifact URL in a build-id variable changes
    SRC_URI's meaning without changing its text, so follow the reference.
    """
    changed = [name for name in names
               if old.get(name, "") != new.get(name, "")]
    through = []
    for name in names:
        text = new.get(name, "") or old.get(name, "")
        if not text or name in changed:
            continue
        for other in differing_blocks:
            if other not in names and _referenced(text, other):
                through.append({"variable": name, "through": other})
    return {
        "changed": bool(changed) or bool(through),
        "differing": changed,
        "changed_through": through,
    }


def classify_surfaces(old: Path, new: Path) -> dict:
    old_path, new_path = Path(old), Path(new)
    old_version = pv_from_name(old_path.name)
    new_version = pv_from_name(new_path.name)
    if not old_version or not new_version:
        raise SurfaceError("both arguments must be versioned ebuild files")

    old_text = _normalize(_read(old_path), old_version)
    new_text = _normalize(_read(new_path), new_version)
    old_blocks = _blocks(old_text)
    new_blocks = _blocks(new_text)

    differing_blocks = {
        name for name in set(old_blocks) | set(new_blocks)
        if old_blocks.get(name, "") != new_blocks.get(name, "")
    }
    surfaces = {
        name: _surface_state(old_blocks, new_blocks, names, differing_blocks)
        for name, names in SURFACE_VARIABLES.items()
    }

    old_phases = {name for name in old_blocks if name.startswith(("src_", "pkg_"))}
    new_phases = {name for name in new_blocks if name.startswith(("src_", "pkg_"))}
    phase_names = sorted(old_phases | new_phases)
    differing_phases = [
        name for name in phase_names
        if old_blocks.get(name, "") != new_blocks.get(name, "")
    ]
    surfaces["phases"] = {
        "changed": bool(differing_phases), "differing": differing_phases,
        "changed_through": []}
    surfaces["installed_layout"] = {
        "changed": any(name in differing_phases for name in INSTALL_PHASES),
        "differing": [n for n in differing_phases if n in INSTALL_PHASES],
        "changed_through": [],
    }

    # Anything that changed and belongs to no surface stays visible: a silent
    # difference would read as "nothing changed" and skip every review.
    attributed = {name for names in SURFACE_VARIABLES.values() for name in names}
    attributed |= set(phase_names)
    unclassified = sorted(differing_blocks - attributed)

    rename_only = old_text == new_text
    changed_surfaces = sorted(
        name for name, state in surfaces.items() if state["changed"])
    header_only = (
        not rename_only and not changed_surfaces
        and unclassified == [HEADER_BLOCK])
    return {
        "ok": True,
        "old": str(old_path),
        "new": str(new_path),
        "old_version": old_version,
        "new_version": new_version,
        "rename_only": rename_only,
        "header_only": header_only,
        "changed_surfaces": changed_surfaces,
        "unclassified_changes": unclassified,
        "surfaces": surfaces,
        "scope": (
            "ebuild text only; an unchanged surface still requires the upstream "
            "release comparison before its review is skipped"
        ),
    }
