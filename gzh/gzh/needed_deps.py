"""Do the libraries an installed package links against appear in its RDEPEND?

Reads the merged package's ``NEEDED.ELF.2`` from the VDB and maps every
soname to the installed package whose ``PROVIDES`` carries it, then checks
each provider against the direct RDEPEND atoms. A provider only reachable
through another dependency's RDEPEND is reported separately: it installs
today, but nothing in the ebuild says so, and the CI image or a desktop
profile having it is what hides the gap.

Runs where the package is installed (the test VM, a build container) and
needs only portage, so it can be copied over as a single file::

    python3 needed_deps.py media-video/tsukimi [--ebuild tsukimi-26.9.2.ebuild]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from portage.dep import Atom, use_reduce
from portage.versions import catpkgsplit

VDB = Path("/var/db/pkg")

# Toolchain and libc: every dynamically linked binary needs them and no
# ebuild lists them.
IMPLICIT = {"sys-libs/glibc", "sys-devel/gcc", "sys-libs/musl", "llvm-runtimes/libgcc",
            "llvm-runtimes/libunwind", "llvm-runtimes/compiler-rt"}
MAX_DEPTH = 6


def _cp(cpv: str) -> str:
    parts = catpkgsplit(cpv)
    return f"{parts[0]}/{parts[1]}" if parts else cpv


def installed_packages(vdb: Path = VDB) -> list[Path]:
    return [p for cat in vdb.iterdir() if cat.is_dir()
            for p in cat.iterdir() if p.is_dir() and re.search(r"-[0-9]", p.name)]


def find_package(cp: str, vdb: Path = VDB) -> Path:
    cat, pn = cp.split("/", 1)
    hits = [p for p in (vdb / cat).glob(f"{pn}-[0-9]*") if _cp(f"{cat}/{p.name}") == cp]
    if not hits:
        raise SystemExit(f"{cp} is not installed under {vdb}")
    if len(hits) > 1:
        raise SystemExit(f"{cp} has several slots installed, pass one: "
                         + " ".join(p.name for p in hits))
    return hits[0]


def needed_sonames(pkgdir: Path) -> tuple[set[str], set[str]]:
    """(sonames the package's objects need, sonames the package itself provides)."""
    needed, own = set(), set()
    text = (pkgdir / "NEEDED.ELF.2").read_text(errors="replace") \
        if (pkgdir / "NEEDED.ELF.2").is_file() else ""
    for line in text.splitlines():
        fields = line.split(";")
        if len(fields) < 5:
            continue
        if fields[2]:
            own.add(fields[2])
        needed.update(s for s in fields[4].split(",") if s)
    return needed - own, own


def providers(vdb: Path = VDB) -> dict[str, set[str]]:
    """soname -> installed packages whose PROVIDES lists it (any ABI)."""
    out: dict[str, set[str]] = {}
    for pkgdir in installed_packages(vdb):
        path = pkgdir / "PROVIDES"
        if not path.is_file():
            continue
        cp = _cp(f"{pkgdir.parent.name}/{pkgdir.name}")
        for line in path.read_text(errors="replace").splitlines():
            _, _, names = line.partition(":")
            for soname in names.split():
                out.setdefault(soname, set()).add(cp)
    return out


def rdepend_cps(text: str) -> set[str]:
    """Every category/package an RDEPEND string can resolve to, USE flags aside."""
    cps = set()
    for token in use_reduce(text or "", matchall=True, flat=True):
        if token in ("||", "^^", "??"):
            continue
        try:
            cps.add(Atom(token).cp)
        except Exception:
            continue
    return cps


def _vdb_rdepend(cp: str, vdb: Path = VDB) -> set[str]:
    try:
        pkgdir = find_package(cp, vdb)
    except SystemExit:
        return set()
    path = pkgdir / "RDEPEND"
    return rdepend_cps(path.read_text(errors="replace")) if path.is_file() else set()


def reachable(direct: set[str], target: str, vdb: Path = VDB) -> list[str] | None:
    """Shortest RDEPEND chain from a direct dependency to target, or None."""
    frontier = [[cp] for cp in sorted(direct)]
    seen = set(direct)
    for _ in range(MAX_DEPTH):
        nxt = []
        for chain in frontier:
            for dep in sorted(_vdb_rdepend(chain[-1], vdb)):
                if dep == target:
                    return chain + [dep]
                if dep not in seen:
                    seen.add(dep)
                    nxt.append(chain + [dep])
        frontier = nxt
        if not frontier:
            break
    return None


def check(cp: str, rdepend: str | None = None, vdb: Path = VDB) -> dict:
    pkgdir = find_package(cp, vdb)
    if rdepend is None:
        rdepend = (pkgdir / "RDEPEND").read_text(errors="replace") \
            if (pkgdir / "RDEPEND").is_file() else ""
    direct = rdepend_cps(rdepend) - {cp}
    needed, _ = needed_sonames(pkgdir)
    provided = providers(vdb)
    findings = []
    for soname in sorted(needed):
        cps = provided.get(soname, set())
        if not cps:
            findings.append({"soname": soname, "provider": None, "state": "unresolved"})
            continue
        if cps & direct:
            continue
        if cps <= IMPLICIT:
            continue
        provider = sorted(cps)[0]
        chain = reachable(direct, provider, vdb)
        # One hop (a direct dependency's own RDEPEND) is what gtk pulling pango
        # looks like and is tolerable. Anything longer is a coincidence of what
        # happens to be installed, so it counts as missing.
        state = "missing"
        if chain and len(chain) == 2:
            state = "transitive"
        findings.append({"soname": soname, "provider": provider,
                         "state": state, "via": chain})
    return {
        "package": cp,
        "direct_rdepend": sorted(direct),
        "needed": sorted(needed),
        "findings": findings,
        "ok": not any(f["state"] in ("missing", "unresolved") for f in findings),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("package", help="category/package, must be installed")
    parser.add_argument("--ebuild", type=Path,
                        help="read RDEPEND from this ebuild instead of the VDB")
    parser.add_argument("--vdb", type=Path, default=VDB)
    args = parser.parse_args(argv)
    rdepend = None
    if args.ebuild:
        text = args.ebuild.read_text(encoding="utf-8")
        text = re.sub(r"\\\n", "", text)
        blocks = {name: value for name, _, value in
                  re.findall(r'^(R?DEPEND)\+?=(["\'])(.*?)\2', text, re.M | re.S)}
        if "RDEPEND" not in blocks:
            print(f"no RDEPEND in {args.ebuild}", file=sys.stderr)
            return 2
        # the common RDEPEND="${DEPEND} ..." / DEPEND="${RDEPEND}" cross-references
        rdepend = blocks["RDEPEND"].replace("${DEPEND}", blocks.get("DEPEND", ""))
        rdepend = rdepend.replace("${RDEPEND}", "")
    report = check(args.package, rdepend, args.vdb)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
