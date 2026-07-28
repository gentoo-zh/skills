import difflib
from pathlib import Path

from gzh.bump import bump_scaffold, diff_ebuild, highest_ebuild


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
