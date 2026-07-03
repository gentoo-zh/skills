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
