import difflib
from pathlib import Path

from gzh.bump import (bump_scaffold, diff_ebuild, highest_ebuild,
                      refresh_copyright_year)


def _pkgdir(tmp_path: Path) -> Path:
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.0.ebuild").write_text('EAPI=8\nDESCRIPTION="x"\nPV_OLD="1.0.0"\n')
    (d / "foo-1.1.0.ebuild").write_text('EAPI=8\nDESCRIPTION="x"\nPV_OLD="1.1.0"\n')
    return d


def test_highest_ebuild(tmp_path):
    d = _pkgdir(tmp_path)
    assert highest_ebuild(d, "foo").name == "foo-1.1.0.ebuild"


def test_highest_ebuild_orders_by_vercmp_not_filename(tmp_path):
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    for pv in ("1.9", "1.10"):
        (d / f"foo-{pv}.ebuild").write_text("EAPI=8\n")
    assert highest_ebuild(d, "foo").name == "foo-1.10.ebuild"


def test_highest_ebuild_skips_live(tmp_path):
    """A live ebuild has no SRC_URI or KEYWORDS, so it is not a bump template."""
    d = tmp_path / "net-proxy" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.ebuild").write_text("EAPI=8\n")
    (d / "foo-9999.ebuild").write_text("EAPI=8\nEGIT_REPO_URI=x\n")
    assert highest_ebuild(d, "foo").name == "foo-1.0.ebuild"


def test_bump_scaffold_refuses_live_only(tmp_path):
    d = tmp_path / "net-proxy" / "foo"
    d.mkdir(parents=True)
    (d / "foo-9999.ebuild").write_text("EAPI=8\nEGIT_REPO_URI=x\n")
    try:
        bump_scaffold(d, "foo", "1.0")
    except FileNotFoundError as exc:
        assert "live-only" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError for a live-only package")


def test_bump_scaffold_copies_highest(tmp_path):
    d = _pkgdir(tmp_path)
    new = bump_scaffold(d, "foo", "1.2.0")
    assert new.name == "foo-1.2.0.ebuild"
    assert new.exists()
    # content copied from highest (1.1.0), filename changed
    assert 'PV_OLD="1.1.0"' in new.read_text()


def test_diff_ebuild(tmp_path):
    d = _pkgdir(tmp_path)
    old = d / "foo-1.0.0.ebuild"
    new = d / "foo-1.1.0.ebuild"
    diff = diff_ebuild(old, new)
    assert "PV_OLD" in diff
    assert diff.startswith("---")


def _write(tmp_path, first_line):
    eb = tmp_path / "foo-1.0.ebuild"
    eb.write_text(f"{first_line}\n# Distributed under the terms of the GNU GPL v2\n\nEAPI=8\n")
    return eb


def test_refresh_copyright_extends_a_range(tmp_path):
    eb = _write(tmp_path, "# Copyright 1999-2024 Gentoo Authors")
    assert refresh_copyright_year(eb, year=2026) is True
    assert eb.read_text().startswith("# Copyright 1999-2026 Gentoo Authors\n")


def test_refresh_copyright_turns_a_single_year_into_a_range(tmp_path):
    eb = _write(tmp_path, "# Copyright 2024 Gentoo Authors")
    assert refresh_copyright_year(eb, year=2026) is True
    assert eb.read_text().startswith("# Copyright 2024-2026 Gentoo Authors\n")


def test_refresh_copyright_leaves_the_current_year_alone(tmp_path):
    eb = _write(tmp_path, "# Copyright 2026 Gentoo Authors")
    assert refresh_copyright_year(eb, year=2026) is False


def test_refresh_copyright_keeps_a_custom_holder(tmp_path):
    eb = _write(tmp_path, "# Copyright 2020-2024 Some Person")
    refresh_copyright_year(eb, year=2026)
    assert eb.read_text().startswith("# Copyright 2020-2026 Some Person\n")


def test_refresh_copyright_ignores_an_unrecognized_first_line(tmp_path):
    eb = _write(tmp_path, "# not a copyright line")
    assert refresh_copyright_year(eb, year=2026) is False
    assert eb.read_text().startswith("# not a copyright line\n")


def test_bump_scaffold_refreshes_the_copied_copyright_year(tmp_path):
    from datetime import date
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.0.ebuild").write_text("# Copyright 1999-2024 Gentoo Authors\nEAPI=8\n")
    new = bump_scaffold(d, "foo", "1.1.0")
    assert new.read_text().startswith(f"# Copyright 1999-{date.today().year} Gentoo Authors\n")
