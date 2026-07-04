from pathlib import Path

from gzh.drop_old import drop_candidates, list_ebuilds


def _pkgdir(tmp_path, versions, pn="foo"):
    d = tmp_path / "cat" / pn
    d.mkdir(parents=True)
    for v in versions:
        (d / f"{pn}-{v}.ebuild").write_text("EAPI=8\n")
    return d


def test_list_ebuilds_globs_by_pn(tmp_path):
    d = _pkgdir(tmp_path, ["1.0", "2.0"])
    ebs = list_ebuilds(d, "foo")
    assert sorted(p.name for p in ebs) == ["foo-1.0.ebuild", "foo-2.0.ebuild"]


def test_drop_keeps_newest_n(tmp_path):
    d = _pkgdir(tmp_path, ["1.0", "1.1", "1.2", "1.3"])
    dropped, kept = drop_candidates(list_ebuilds(d, "foo"), "foo", keep=2)
    assert [p.name for p in dropped] == ["foo-1.0.ebuild", "foo-1.1.ebuild"]
    assert [p.name for p in kept] == ["foo-1.2.ebuild", "foo-1.3.ebuild"]


def test_drop_vercmp_not_lexicographic(tmp_path):
    # vercmp: 1.10 > 1.9 ; lexicographic would wrongly put 1.9 newer
    d = _pkgdir(tmp_path, ["1.9", "1.10"])
    dropped, kept = drop_candidates(list_ebuilds(d, "foo"), "foo", keep=1)
    assert [p.name for p in dropped] == ["foo-1.9.ebuild"]
    assert [p.name for p in kept] == ["foo-1.10.ebuild"]


def test_drop_keeps_9999_liveup_always(tmp_path):
    d = _pkgdir(tmp_path, ["1.0", "2.0", "9999"])
    dropped, kept = drop_candidates(list_ebuilds(d, "foo"), "foo", keep=1)
    assert [p.name for p in dropped] == ["foo-1.0.ebuild"]
    assert sorted(p.name for p in kept) == ["foo-2.0.ebuild", "foo-9999.ebuild"]


def test_drop_revision_sort(tmp_path):
    d = _pkgdir(tmp_path, ["1.0", "1.0-r1", "1.0-r2"])
    dropped, kept = drop_candidates(list_ebuilds(d, "foo"), "foo", keep=1)
    assert [p.name for p in dropped] == ["foo-1.0.ebuild", "foo-1.0-r1.ebuild"]
    assert [p.name for p in kept] == ["foo-1.0-r2.ebuild"]


def test_drop_nothing_when_within_keep(tmp_path):
    d = _pkgdir(tmp_path, ["1.0", "2.0"])
    dropped, kept = drop_candidates(list_ebuilds(d, "foo"), "foo", keep=2)
    assert dropped == []
    assert sorted(p.name for p in kept) == ["foo-1.0.ebuild", "foo-2.0.ebuild"]
