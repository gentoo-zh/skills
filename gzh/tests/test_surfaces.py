import pytest
from click.testing import CliRunner

from gzh.cli import cli
from gzh.surfaces import SurfaceError, classify_surfaces

HEADER = "# Copyright 2026 Gentoo Authors\n# Distributed under the terms of the GNU General Public License v2\n\n"

BODY = """EAPI=8

inherit toolchain-funcs

DESCRIPTION="example"
HOMEPAGE="https://example.invalid"
SRC_URI="https://example.invalid/${P}.tar.gz"
LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64"
IUSE="test"
RDEPEND="dev-libs/libfoo"

src_install() {
\tdefault
}
"""


def _pair(tmp_path, old_version, new_version, old=BODY, new=None,
          old_header=HEADER, new_header=None):
    old_path = tmp_path / f"example-{old_version}.ebuild"
    new_path = tmp_path / f"example-{new_version}.ebuild"
    old_path.write_text(old_header + old, encoding="utf-8")
    new_path.write_text(
        (new_header if new_header is not None else old_header)
        + (new if new is not None else old), encoding="utf-8")
    return old_path, new_path


def test_pure_rename_reports_no_changed_surface(tmp_path):
    report = classify_surfaces(*_pair(tmp_path, "1.0", "1.1"))

    assert report["rename_only"] is True
    assert report["changed_surfaces"] == []
    assert report["unclassified_changes"] == []


def test_literal_version_in_body_still_counts_as_a_rename(tmp_path):
    body = BODY.replace("${P}", "example-1.0")
    report = classify_surfaces(
        *_pair(tmp_path, "1.0", "1.1", old=body,
               new=body.replace("example-1.0", "example-1.1")))

    assert report["rename_only"] is True


def test_dependency_change_routes_only_that_surface(tmp_path):
    report = classify_surfaces(*_pair(
        tmp_path, "1.0", "1.1",
        new=BODY.replace('RDEPEND="dev-libs/libfoo"',
                         'RDEPEND="dev-libs/libfoo\n\tdev-libs/libbar"')))

    assert report["rename_only"] is False
    assert report["changed_surfaces"] == ["dependencies"]
    assert report["surfaces"]["dependencies"]["differing"] == ["RDEPEND"]
    assert report["surfaces"]["phases"]["changed"] is False


def test_install_phase_change_reports_installed_layout(tmp_path):
    report = classify_surfaces(*_pair(
        tmp_path, "1.0", "1.1",
        new=BODY.replace("\tdefault\n", "\tdefault\n\tdodoc README\n")))

    assert "installed_layout" in report["changed_surfaces"]
    assert report["surfaces"]["installed_layout"]["differing"] == ["src_install"]


def test_build_id_variable_propagates_to_the_artifact_surface(tmp_path):
    body = BODY.replace(
        'SRC_URI="https://example.invalid/${P}.tar.gz"',
        'MY_BUILD="1000"\nSRC_URI="https://example.invalid/${MY_BUILD}.tar.gz"')
    report = classify_surfaces(*_pair(
        tmp_path, "1.0", "1.1", old=body,
        new=body.replace('MY_BUILD="1000"', 'MY_BUILD="2000"')))

    assert report["changed_surfaces"] == ["artifacts"]
    assert report["unclassified_changes"] == ["MY_BUILD"]
    assert report["surfaces"]["artifacts"]["changed_through"] == [
        {"variable": "SRC_URI", "through": "MY_BUILD"}]


def test_copyright_refresh_is_reported_rather_than_swallowed(tmp_path):
    report = classify_surfaces(*_pair(
        tmp_path, "1.0", "1.1",
        new_header=HEADER.replace("2026", "2025-2026")))

    assert report["rename_only"] is False
    assert report["header_only"] is True
    assert report["changed_surfaces"] == []
    assert report["unclassified_changes"] == ["header"]


def test_unversioned_argument_is_rejected(tmp_path):
    plain = tmp_path / "example.ebuild"
    plain.write_text(HEADER + BODY, encoding="utf-8")
    versioned = tmp_path / "example-1.0.ebuild"
    versioned.write_text(HEADER + BODY, encoding="utf-8")

    with pytest.raises(SurfaceError, match="versioned ebuild files"):
        classify_surfaces(plain, versioned)


def test_oversized_ebuild_is_rejected(tmp_path):
    old_path, new_path = _pair(tmp_path, "1.0", "1.1")
    new_path.write_text("EAPI=8\n" + "#" * (512 * 1024), encoding="utf-8")

    with pytest.raises(SurfaceError, match="exceeds"):
        classify_surfaces(old_path, new_path)


def test_cli_reports_the_classification(tmp_path):
    old_path, new_path = _pair(tmp_path, "1.0", "1.1")

    result = CliRunner().invoke(cli, ["surfaces", str(old_path), str(new_path)])

    assert result.exit_code == 0
    assert '"rename_only": true' in result.output
    assert '"changed_surfaces": []' in result.output
