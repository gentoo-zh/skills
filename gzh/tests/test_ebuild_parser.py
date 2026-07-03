from pathlib import Path

from gzh.ebuild_parser import parse_ebuild

SAMPLE = '''# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="Example package"
HOMEPAGE="https://example.org/${PN}"
SRC_URI="https://example.org/${P}.tar.gz"
LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64 ~arm64"
IUSE="test"
'''


def test_parse_basic(tmp_path):
    eb = tmp_path / "foo-1.2.3.ebuild"
    eb.write_text(SAMPLE)
    info = parse_ebuild(eb)
    assert info["EAPI"] == "8"
    assert info["PV"] == "1.2.3"
    assert "~amd64" in info["KEYWORDS"]
    assert info["LICENSE"] == "MIT"
    assert info["SRC_URI"] == "https://example.org/${P}.tar.gz"


def test_pv_from_revision(tmp_path):
    eb = tmp_path / "foo-1.2.3-r1.ebuild"
    eb.write_text("EAPI=8\n")
    assert parse_ebuild(eb)["PV"] == "1.2.3-r1"


def test_parse_inherit(tmp_path):
    eb = tmp_path / "foo-1.0.ebuild"
    eb.write_text("EAPI=8\ninherit distutils-r1 pypi\n")
    assert parse_ebuild(eb)["inherit"] == ["distutils-r1", "pypi"]
