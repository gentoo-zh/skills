from pathlib import Path
import subprocess

from gzh.drop_old import _enumerate_pkgs, drop_candidates, list_ebuilds, run_drop_old


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


def _overlay(tmp_path):
    # foo has multiple versions; bar has only one.
    foo = tmp_path / "cat" / "foo"
    foo.mkdir(parents=True)
    for v in ["1.0", "1.1", "1.2"]:
        (foo / f"foo-{v}.ebuild").write_text("EAPI=8\n")
    bar = tmp_path / "cat" / "bar"
    bar.mkdir(parents=True)
    (bar / "bar-1.0.ebuild").write_text("EAPI=8\n")
    notpkg = tmp_path / "cat" / "empty"
    notpkg.mkdir(parents=True)
    return tmp_path


def test_enumerate_pkgs_all(tmp_path):
    root = _overlay(tmp_path)
    assert _enumerate_pkgs(root, "all") == ["cat/bar", "cat/foo"]


def test_enumerate_pkgs_single(tmp_path):
    assert _enumerate_pkgs(tmp_path, "cat/foo") == ["cat/foo"]


def test_run_drop_old_dry_run_does_not_delete(tmp_path):
    root = _overlay(tmp_path)
    res = run_drop_old("all", keep=2, apply=False, overlay_root=root)
    assert res["ok"] is True
    by_pkg = {r["cat_pkg"]: r for r in res["results"]}
    assert by_pkg["cat/foo"]["dropped"] == ["foo-1.0.ebuild"]
    assert by_pkg["cat/bar"]["dropped"] == []
    # dry-run: files still there
    assert (root / "cat" / "foo" / "foo-1.0.ebuild").exists()


def test_run_drop_old_apply_is_disabled_without_deleting(tmp_path):
    root = _overlay(tmp_path)
    before = {path.relative_to(root): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    try:
        run_drop_old("all", keep=2, apply=True, overlay_root=root)
    except ValueError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("expected destructive apply mode to be disabled")
    after = {path.relative_to(root): path.read_bytes()
             for path in root.rglob("*") if path.is_file()}
    assert after == before


from click.testing import CliRunner

from gzh.cli import cli


def test_drop_old_help_registered():
    result = CliRunner().invoke(cli, ["drop-old", "--help"])
    assert result.exit_code == 0
    assert "keep" in result.output.lower()


def test_drop_old_requires_all_or_pkg():
    result = CliRunner().invoke(cli, ["drop-old"])
    assert result.exit_code != 0  # mutual exclusion / required


def test_drop_old_all_and_pkg_mutually_exclusive():
    result = CliRunner().invoke(cli, ["drop-old", "--all", "--pkg", "a/b"])
    assert result.exit_code != 0


def test_drop_old_dry_run_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    foo = tmp_path / "cat" / "foo"
    foo.mkdir(parents=True)
    for v in ["1.0", "1.1", "1.2"]:
        (foo / f"foo-{v}.ebuild").write_text("EAPI=8\n")
    result = CliRunner().invoke(cli_mod.cli,
                                ["drop-old", "--pkg", "cat/foo", "--keep", "2"])
    assert result.exit_code == 0
    assert "foo-1.0.ebuild" in result.output  # listed as dropped
    assert (foo / "foo-1.0.ebuild").exists()  # dry-run: not deleted


def test_cli_apply_is_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    foo = tmp_path / "cat" / "foo"
    foo.mkdir(parents=True)
    for v in ["1.0", "1.1", "1.2"]:
        (foo / f"foo-{v}.ebuild").write_text("EAPI=8\n")
    result = CliRunner().invoke(cli, ["drop-old", "--pkg", "cat/foo", "--apply"])
    assert result.exit_code == 2
    assert "disabled" in result.output
    assert (foo / "foo-1.0.ebuild").exists()


def test_cli_pkg_format_validation():
    # I-2: --pkg without category slash -> clean UsageError, not a crash
    result = CliRunner().invoke(cli, ["drop-old", "--pkg", "foo"])
    assert result.exit_code != 0
    assert "cat/pkg" in result.output
