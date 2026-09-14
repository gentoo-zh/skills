"""Toolchain floors and pre-stripped binaries, checked before any build.

Mirrors the merge-time QA checks that the emerge-on-PR elog gate turns into a
red run, so that a bump can be corrected before pushing:

* ``install-qa-check.d/60go-module-eclass`` — the ``go`` line of every go.mod
  in WORKDIR versus the ``>=dev-lang/go-X`` floor in BDEPEND/DEPEND.
* ``install-qa-check.d/60cargo-eclass`` — ``edition`` and ``rust-version`` of
  every Cargo.toml in WORKDIR (vendored crates included) versus RUST_MIN_VER.
* ``estrip`` — ELF objects that already lack ``.symtab`` become a
  "Pre-stripped files found" notice unless QA_PRESTRIPPED or RESTRICT=strip
  covers them.

All three read the unpacked distfiles, so this needs the archives present in
DISTDIR but no dependency, no sandbox and no compiler. The comparisons use the
same regular expressions and the same version comparison as the checks they
predict, so a clean result here is the same verdict CI will reach for these
three notices, not a stand-in for the build itself.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from portage.versions import vercmp

from gzh.ebuild_parser import parse_ebuild
from gzh.manifest import _pv_subs, extract_src_uri_map, parse_manifest_dist

# First Rust release that understands each edition, as 60cargo-eclass has it.
RUST_EDITIONS = {"2024": "1.85.0"}

_GO_MOD_LINE = re.compile(r"^go\s+([0-9.]*)\s*$", re.M)
_GO_DEP = re.compile(r">=?dev-lang/go-([0-9.]+)")
_CARGO_EDITION = re.compile(r'^\s*edition\s*=\s*"([0-9]*)"\s*$', re.M)
_CARGO_RUST_VERSION = re.compile(r'^\s*rust-version\s*=\s*"([0-9.]*)"\s*$', re.M)
_ARCHIVE_SUFFIXES = (
    ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
    ".tar.zst", ".zip", ".deb", ".rpm", ".crate", ".gh.tar.gz",
)


class FloorError(RuntimeError):
    """The check could not run; distinct from the ebuild being wrong."""


def _max_version(values: list[str]) -> str | None:
    best = None
    for value in values:
        if best is None or (vercmp(value, best) or 0) > 0:
            best = value
    return best


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def go_floor(workdir: Path, ebuild_deps: str) -> dict | None:
    """The 60go-module-eclass verdict over an unpacked WORKDIR."""
    required = _max_version([
        m.group(1)
        for path in workdir.rglob("go.mod")
        for m in _GO_MOD_LINE.finditer(_text(path))
        if m.group(1)
    ])
    if required is None:
        return None
    declared = _max_version(_GO_DEP.findall(ebuild_deps))
    short = declared is None or (vercmp(declared, required) or 0) < 0
    return {
        "rule": "go-floor",
        "required": required,
        "declared": declared,
        "ok": not short,
        "fix": f'BDEPEND=">=dev-lang/go-{required}"' if short else None,
    }


def rust_floor(workdir: Path, rust_min_ver: str) -> dict | None:
    """The 60cargo-eclass verdict over an unpacked WORKDIR."""
    texts = [_text(path) for path in workdir.rglob("Cargo.toml")]
    editions = [m.group(1) for text in texts for m in _CARGO_EDITION.finditer(text)]
    edition = max(editions, key=int) if editions else None
    required, source = None, None
    if edition and edition in RUST_EDITIONS:
        required = RUST_EDITIONS[edition]
        source = f'edition="{edition}"'
    rust_versions = [m.group(1) for text in texts
                     for m in _CARGO_RUST_VERSION.finditer(text) if m.group(1)]
    newest = _max_version(rust_versions)
    if newest and (required is None or (vercmp(required, newest) or 0) < 0):
        required, source = newest, f'rust-version="{newest}"'
    if required is None:
        return None
    declared = rust_min_ver or None
    short = (vercmp(declared or "0", required) or 0) < 0
    return {
        "rule": "rust-floor",
        "required": required,
        "source": source,
        "declared": declared,
        "ok": not short,
        "fix": f'RUST_MIN_VER="{required}"' if short else None,
    }


def prestripped(workdir: Path, scanelf: str | None = None) -> dict | None:
    """ELF files under WORKDIR that estrip would report as pre-stripped.

    Uses the same scanelf query estrip does; None when scanelf is unavailable
    so the caller can say the check did not run rather than that it passed.
    """
    scanelf = scanelf or shutil.which("scanelf")
    if not scanelf:
        return None
    proc = subprocess.run(
        [scanelf, "-yqRF", "#k%F", "-k", "!.symtab", str(workdir)],
        capture_output=True, text=True)
    files = sorted(
        str(Path(line).relative_to(workdir))
        for line in proc.stdout.splitlines() if line.strip())
    return {"rule": "prestripped", "files": files, "ok": not files}


def _distfiles_for(ebuild: Path) -> list[str]:
    text = ebuild.read_text(encoding="utf-8")
    names = set(extract_src_uri_map(text, _pv_subs(ebuild.name)))
    manifest = ebuild.parent / "Manifest"
    listed = {d["name"] for d in parse_manifest_dist(_text(manifest))}
    # GIT_CRATES and other eclass-generated names never appear in SRC_URI
    # text; the Manifest is the only place that knows them. Take what the
    # Manifest lists for this version on top of what SRC_URI names.
    pv = _pv_subs(ebuild.name).get("PV", "")
    for name in listed:
        if pv and pv in name:
            names.add(name)
    return sorted(names & listed) if listed else sorted(names)


def unpack(distfiles: list[str], distdir: Path, workdir: Path) -> list[str]:
    """Extract every archive among distfiles into workdir; returns what it skipped."""
    bsdtar = shutil.which("bsdtar")
    if not bsdtar:
        raise FloorError("bsdtar (app-arch/libarchive) is required to unpack distfiles")
    skipped = []
    for name in distfiles:
        source = distdir / name
        if not source.is_file():
            raise FloorError(f"distfile missing: {source}")
        if not name.endswith(_ARCHIVE_SUFFIXES):
            skipped.append(name)
            continue
        target = workdir / name
        target.mkdir()
        proc = subprocess.run([bsdtar, "-xf", str(source), "-C", str(target)],
                              capture_output=True, text=True)
        if proc.returncode:
            raise FloorError(f"cannot unpack {name}: {proc.stderr.strip()}")
    return skipped


def check_floors(ebuild: Path, distdir: Path, keep: Path | None = None) -> dict:
    """Run every applicable floor check for one ebuild.

    ``keep`` names a directory to unpack into (left in place for inspection);
    otherwise a temporary directory is used and removed.
    """
    ebuild = Path(ebuild).resolve()
    parsed = parse_ebuild(ebuild)
    inherit = set(parsed.get("inherit", []))
    distfiles = _distfiles_for(ebuild)
    if not distfiles:
        raise FloorError("no distfiles resolved from SRC_URI or Manifest")

    def run(workdir: Path) -> dict:
        skipped = unpack(distfiles, Path(distdir), workdir)
        findings = []
        if "go-module" in inherit:
            found = go_floor(workdir, " ".join(
                parsed.get(k, "") for k in ("BDEPEND", "DEPEND")))
            if found:
                findings.append(found)
        if "cargo" in inherit:
            found = rust_floor(workdir, parsed.get("RUST_MIN_VER", ""))
            if found:
                findings.append(found)
        stripped = prestripped(workdir)
        if stripped is None:
            skipped.append("prestripped: scanelf not found")
        elif stripped["files"]:
            covered = bool(parsed.get("QA_PRESTRIPPED")) or \
                "strip" in parsed.get("RESTRICT", "").split()
            stripped["ok"] = covered
            stripped["fix"] = None if covered else \
                'QA_PRESTRIPPED="..." for the listed files, or stop the build stripping'
            findings.append(stripped)
        return {
            "ebuild": str(ebuild),
            "distfiles": distfiles,
            "skipped": skipped,
            "findings": findings,
            "ok": all(f["ok"] for f in findings),
        }

    if keep is not None:
        keep = Path(keep)
        keep.mkdir(parents=True, exist_ok=True)
        return run(keep)
    with tempfile.TemporaryDirectory(prefix="gzh-floors-") as tmp:
        return run(Path(tmp))
