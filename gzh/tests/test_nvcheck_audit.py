from gzh.nvcheck_audit import audit, infer_source


def test_infer_github_from_homepage():
    parsed = {"HOMEPAGE": "https://github.com/org/foo", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "github"
    assert entry == {"source": "github", "github": "org/foo", "use_latest_release": True}


def test_infer_github_strips_trailing_git_and_slash():
    parsed = {"HOMEPAGE": "https://github.com/org/foo.git", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert entry["github"] == "org/foo"


def test_infer_pypi_from_src_uri():
    parsed = {"HOMEPAGE": "https://example.org", "SRC_URI": "https://pypi.org/foo", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "pypi"
    assert entry == {"source": "pypi", "pypi": "foo"}


def test_infer_pypi_from_inherit():
    parsed = {"HOMEPAGE": "", "SRC_URI": "", "inherit": ["distutils-r1", "pypi"]}
    source, entry = infer_source(parsed, "foo")
    assert source == "pypi"
    assert entry["pypi"] == "foo"


def test_infer_git_from_gitlab_url():
    parsed = {"HOMEPAGE": "https://gitlab.com/org/foo", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "git"
    assert entry["source"] == "git"
    assert entry["use_max_tag"] is True
    assert entry["src"]  # non-empty: gitlab URL or homepage


def test_infer_git_src_from_dot_git_url():
    parsed = {"HOMEPAGE": "https://example.org/proj",
              "SRC_URI": "https://example.org/proj-1.0.tar.gz", "inherit": []}
    # explicit .git clone URL in HOMEPAGE wins the src
    parsed2 = {"HOMEPAGE": "https://git.example.org/org/foo.git",
               "SRC_URI": "", "inherit": []}
    _, entry = infer_source(parsed2, "foo")
    assert entry["src"] == "https://git.example.org/org/foo.git"


def test_infer_github_dotted_repo():
    parsed = {"HOMEPAGE": "https://github.com/org/foo.bar", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo.bar")
    assert entry["github"] == "org/foo.bar"


def test_infer_unknown_when_no_match():
    parsed = {"HOMEPAGE": "https://example.org", "SRC_URI": "https://example.org/foo.tar.gz", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "unknown"
    assert entry is None


def test_infer_github_priority_over_pypi():
    # both github homepage and pypi src_uri -> github wins
    parsed = {"HOMEPAGE": "https://github.com/org/foo", "SRC_URI": "https://pypi.org/foo", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "github"


def test_audit_stale_and_missing():
    configured = {"cat/a", "cat/b", "cat/removed"}
    actual = {"cat/a", "cat/b", "cat/new"}
    stale, missing = audit(configured, actual, filter_system=False)
    assert stale == ["cat/removed"]
    assert missing == ["cat/new"]


def test_audit_filters_system_packages_by_default():
    configured = {"cat/a"}
    actual = {"cat/a", "acct-group/x", "virtual/y", "cat/missing"}
    stale, missing = audit(configured, actual, filter_system=True)
    assert missing == ["cat/missing"]  # acct-group/virtual filtered


def test_audit_no_filter_includes_system():
    configured = {"cat/a"}
    actual = {"cat/a", "acct-group/x", "cat/missing"}
    _, missing = audit(configured, actual, filter_system=False)
    assert sorted(missing) == ["acct-group/x", "cat/missing"]


def test_audit_empty_when_consistent():
    configured = {"cat/a"}
    actual = {"cat/a"}
    stale, missing = audit(configured, actual)
    assert stale == [] and missing == []


import tomllib
from pathlib import Path

from gzh.nvcheck_audit import _enumerate_actual, _load_configured, run_audit


def _overlay(tmp_path: Path) -> Path:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "overlay.toml").write_text(
        '__config__ = {newver = "n.json"}\n["cat/cfg"]\nsource = "github"\n'
        'github = "o/cfg"\n', encoding="utf-8")
    # actual: cat/cfg (configured), cat/gh (missing, github), cat/pyp (missing, pypi),
    # cat/unk (missing unknown), cat/cfg-rm not in actual (stale handled by set diff)
    for pkg, homepage in [("cat/cfg", "https://github.com/o/cfg"),
                          ("cat/gh", "https://github.com/o/gh"),
                          ("cat/pyp", "https://pypi.org/pyp"),
                          ("cat/unk", "https://example.org")]:
        d = tmp_path / pkg
        d.mkdir(parents=True)
        pn = pkg.split("/")[1]
        (d / f"{pn}-1.0.ebuild").write_text(
            f'EAPI=8\nHOMEPAGE="{homepage}"\nSRC_URI=""\nSLOT="0"\n', encoding="utf-8")
    return tmp_path


def test_load_configured(tmp_path):
    root = _overlay(tmp_path)
    cfg = _load_configured(root / ".github" / "workflows" / "overlay.toml")
    assert cfg == {"cat/cfg"}


def test_enumerate_actual(tmp_path):
    root = _overlay(tmp_path)
    actual = _enumerate_actual(root)
    assert actual == {"cat/cfg", "cat/gh", "cat/pyp", "cat/unk"}


def test_run_audit_dry_run(tmp_path):
    root = _overlay(tmp_path)
    set_calls = []
    res = run_audit(apply=False, overlay_root=root,
                    set_entry_fn=lambda *a, **k: set_calls.append(a))
    assert res["ok"] is True
    assert res["stale"] == []  # cat/cfg exists in both
    sources = {m["cat_pkg"]: m["source"] for m in res["missing"]}
    assert sources["cat/gh"] == "github"
    assert sources["cat/pyp"] == "pypi"
    assert res["skipped_unknown"] == ["cat/unk"]
    assert all(m["applied"] is False for m in res["missing"])
    assert set_calls == []  # dry-run: no set calls


def test_run_audit_apply_sets_entries(tmp_path):
    root = _overlay(tmp_path)
    set_calls = []
    res = run_audit(apply=True, overlay_root=root,
                    set_entry_fn=lambda toml, cat_pkg, entry: set_calls.append((cat_pkg, entry)))
    applied_pkgs = [c[0] for c in set_calls]
    assert "cat/gh" in applied_pkgs and "cat/pyp" in applied_pkgs
    assert "cat/unk" not in applied_pkgs  # unknown skipped
    assert all(m["applied"] is True for m in res["missing"])


from click.testing import CliRunner

from gzh.cli import cli


def test_nvcheck_audit_help_registered():
    result = CliRunner().invoke(cli, ["nvcheck-audit", "--help"])
    assert result.exit_code == 0
    assert "apply" in result.output.lower()


def test_nvcheck_audit_dry_run_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    # minimal overlay with one missing github pkg
    (tmp_path / "cat" / "gh").mkdir(parents=True)
    (tmp_path / "cat" / "gh" / "gh-1.0.ebuild").write_text(
        'EAPI=8\nHOMEPAGE="https://github.com/o/gh"\nSRC_URI=""\nSLOT="0"\n')
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "overlay.toml").write_text('__config__ = {newver="n.json"}\n', encoding="utf-8")
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    result = CliRunner().invoke(cli_mod.cli, ["nvcheck-audit"])
    assert result.exit_code == 0
    assert "cat/gh" in result.output
    assert '"applied": false' in result.output  # dry-run


def test_run_audit_overlay_toml_error(tmp_path):
    # overlay.toml missing -> ok=False, error mentions overlay.toml
    res = run_audit(overlay_root=tmp_path)
    assert res["ok"] is False
    assert "overlay.toml" in res["error"]
    assert res["stale"] == [] and res["missing"] == []


def test_cli_overlay_error_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    result = CliRunner().invoke(cli, ["nvcheck-audit"])
    assert result.exit_code == 1
    assert '"ok": false' in result.output


def test_run_audit_skips_bad_ebuild(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "overlay.toml").write_text('__config__ = {newver="n.json"}\n', encoding="utf-8")
    # good pkg parses fine
    good = tmp_path / "cat" / "good"
    good.mkdir(parents=True)
    (good / "good-1.0.ebuild").write_text(
        'EAPI=8\nHOMEPAGE="https://github.com/o/good"\nSRC_URI=""\nSLOT="0"\n',
        encoding="utf-8")
    # bad pkg: invalid UTF-8 so parse_ebuild throws
    bad = tmp_path / "cat" / "bad"
    bad.mkdir(parents=True)
    (bad / "bad-1.0.ebuild").write_bytes(b"\xff\xfe\x00not utf-8")
    res = run_audit(overlay_root=tmp_path)
    assert res["ok"] is True
    assert "cat/bad" in res["skipped_unknown"]
    assert any(m["cat_pkg"] == "cat/good" for m in res["missing"])
