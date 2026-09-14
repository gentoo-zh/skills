import io
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from gzh.floors import (FloorError, check_floors, go_floor, prestripped,
                        rust_floor)


def _tar(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


# -- go ---------------------------------------------------------------------

def test_go_floor_takes_the_newest_go_mod_and_the_full_version_string(tmp_path):
    _write(tmp_path, {
        "netbird-0.78.1/go.mod": "module x\n\ngo 1.26.0\n\ntoolchain go1.26.7\n",
        "netbird-0.78.1/mobile/go.mod": "module y\n\ngo 1.24.0\n",
    })
    found = go_floor(tmp_path, ">=dev-lang/go-1.26")
    # ver_test 1.26 -lt 1.26.0 is true, so the check wants the three-part string
    assert found["required"] == "1.26.0"
    assert found["declared"] == "1.26"
    assert not found["ok"]
    assert found["fix"] == 'BDEPEND=">=dev-lang/go-1.26.0"'


def test_go_floor_accepts_an_equal_or_higher_declared_floor(tmp_path):
    _write(tmp_path, {"a/go.mod": "go 1.26.0\n"})
    assert go_floor(tmp_path, ">=dev-lang/go-1.26.0")["ok"]
    assert go_floor(tmp_path, ">=dev-lang/go-1.27.1:=")["ok"]


def test_go_floor_reads_the_highest_of_several_declared_atoms(tmp_path):
    _write(tmp_path, {"a/go.mod": "go 1.26.0\n"})
    assert go_floor(tmp_path, ">=dev-lang/go-1.24 >=dev-lang/go-1.26.0")["ok"]


def test_go_floor_is_silent_without_a_go_mod(tmp_path):
    assert go_floor(tmp_path, ">=dev-lang/go-1.26.0") is None


# -- rust -------------------------------------------------------------------

def test_rust_floor_uses_the_newest_rust_version_across_vendored_crates(tmp_path):
    _write(tmp_path, {
        "euphonica/Cargo.toml": '[package]\nedition = "2024"\n',
        "crates/a-1.0.0/Cargo.toml": 'rust-version = "1.88.0"\n',
        "crates/b-2.0.0/Cargo.toml": 'rust-version = "1.92.0"\n',
    })
    found = rust_floor(tmp_path, "1.88.0")
    assert found["required"] == "1.92.0"
    assert found["source"] == 'rust-version="1.92.0"'
    assert not found["ok"]
    assert found["fix"] == 'RUST_MIN_VER="1.92.0"'
    assert rust_floor(tmp_path, "1.92.0")["ok"]


def test_rust_floor_falls_back_to_the_edition_floor(tmp_path):
    _write(tmp_path, {"x/Cargo.toml": 'edition = "2024"\n'})
    found = rust_floor(tmp_path, "")
    assert found["required"] == "1.85.0"
    assert found["source"] == 'edition="2024"'
    assert not found["ok"]
    assert rust_floor(tmp_path, "1.85.0")["ok"]


def test_rust_floor_ignores_editions_it_has_no_floor_for(tmp_path):
    _write(tmp_path, {"x/Cargo.toml": 'edition = "2021"\n'})
    assert rust_floor(tmp_path, "") is None


# -- prestripped ------------------------------------------------------------

def test_prestripped_reports_nothing_when_scanelf_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert prestripped(tmp_path) is None


@pytest.mark.skipif(shutil.which("scanelf") is None or shutil.which("cc") is None,
                    reason="needs scanelf and a C compiler")
def test_prestripped_lists_only_objects_without_a_symtab(tmp_path):
    (tmp_path / "hello.c").write_text("int main(void){return 0;}\n")
    subprocess.run(["cc", "-o", str(tmp_path / "unstripped"), str(tmp_path / "hello.c")],
                   check=True)
    subprocess.run(["cc", "-s", "-o", str(tmp_path / "stripped"), str(tmp_path / "hello.c")],
                   check=True)
    found = prestripped(tmp_path)
    assert found["files"] == ["stripped"]
    assert not found["ok"]


# -- end to end -------------------------------------------------------------

def _package(tmp_path: Path, ebuild_text: str, dists: dict[str, dict[str, bytes]]):
    pkg = tmp_path / "overlay" / "net-vpn" / "demo"
    pkg.mkdir(parents=True)
    (pkg / "demo-1.0.ebuild").write_text(ebuild_text)
    distdir = tmp_path / "distfiles"
    distdir.mkdir()
    manifest = []
    for name, files in dists.items():
        _tar(distdir / name, files)
        manifest.append(f"DIST {name} {(distdir / name).stat().st_size} BLAKE2B 0 SHA512 0")
    (pkg / "Manifest").write_text("\n".join(manifest) + "\n")
    return pkg / "demo-1.0.ebuild", distdir


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="needs bsdtar")
def test_check_floors_unpacks_every_archive_the_manifest_lists(tmp_path):
    ebuild, distdir = _package(tmp_path, (
        'EAPI=8\ninherit go-module\n'
        'SRC_URI="https://x/${P}.tar.gz https://y/${P}-vendor.tar.gz"\n'
        'BDEPEND=">=dev-lang/go-1.25.5"\n'
    ), {
        "demo-1.0.tar.gz": {"demo-1.0/go.mod": b"go 1.25.5\n"},
        "demo-1.0-vendor.tar.gz": {"vendor/x/go.mod": b"go 1.26.0\n"},
    })
    report = check_floors(ebuild, distdir)
    assert report["distfiles"] == ["demo-1.0-vendor.tar.gz", "demo-1.0.tar.gz"]
    assert not report["ok"]
    [found] = report["findings"]
    assert found["rule"] == "go-floor" and found["required"] == "1.26.0"


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="needs bsdtar")
def test_check_floors_picks_up_manifest_only_names_such_as_git_crates(tmp_path):
    ebuild, distdir = _package(tmp_path, (
        'EAPI=8\ninherit cargo\nRUST_MIN_VER="1.88.0"\n'
        'SRC_URI="https://x/${P}.tar.gz"\n'
    ), {
        "demo-1.0.tar.gz": {"demo-1.0/Cargo.toml": b'edition = "2021"\n'},
        "rust-mpd-1.0abc.gh.tar.gz": {"rust-mpd/Cargo.toml": b'rust-version = "1.90.0"\n'},
    })
    report = check_floors(ebuild, distdir)
    assert "rust-mpd-1.0abc.gh.tar.gz" in report["distfiles"]
    [found] = report["findings"]
    assert found["required"] == "1.90.0" and not found["ok"]


@pytest.mark.skipif(shutil.which("bsdtar") is None, reason="needs bsdtar")
def test_check_floors_is_clean_when_the_ebuild_already_declares_the_floor(tmp_path):
    ebuild, distdir = _package(tmp_path, (
        'EAPI=8\ninherit cargo\nRUST_MIN_VER="1.92.0"\n'
        'SRC_URI="https://x/${P}.tar.gz"\n'
    ), {"demo-1.0.tar.gz": {"demo-1.0/Cargo.toml": b'rust-version = "1.92.0"\n'}})
    report = check_floors(ebuild, distdir)
    assert report["ok"]
    assert [f["rule"] for f in report["findings"]] == ["rust-floor"]


def test_check_floors_refuses_to_run_without_the_distfile(tmp_path):
    ebuild, distdir = _package(tmp_path, (
        'EAPI=8\ninherit cargo\nSRC_URI="https://x/${P}.tar.gz"\n'
    ), {"demo-1.0.tar.gz": {"demo-1.0/Cargo.toml": b""}})
    (distdir / "demo-1.0.tar.gz").unlink()
    with pytest.raises(FloorError, match="distfile missing"):
        check_floors(ebuild, distdir)
