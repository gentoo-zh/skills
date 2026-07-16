"""Tests for the validated-lesson code changes folded into gzh:
elog/QA-notice surfacing, DeadUrl reverify + --net/--commits, truncated-distfile size
guard, DCO sign-off, and the triage skip/escalate distinction."""
import subprocess
from pathlib import Path

from click.testing import CliRunner

import gzh.cli as cli_mod
from gzh.buildtest import run_build_test, scan_qa_notices
from gzh.commit import run_commit
from gzh.manifest import (extract_src_uri_map, parse_manifest_dist, _pv_subs,
                          verify_manifest_sizes)
from gzh.pkgcheck import (reverify_url_findings, run_pkgcheck,
                          run_pkgcheck_commits)
from gzh.triage import list_skipped, skip_issue


def _eb(tmp_path):
    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    return eb


# ---- P1-24: DCO sign-off ----
def test_commit_adds_signoff(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
               message="cat/foo: add 1", runner=fake_run)
    assert "--signoff" in seen["args"]
    assert "--gpg-sign" not in seen["args"]  # not forced


# ---- P2-29: QA-notice surfacing from the combined stream, deferring sonames ----
def test_scan_qa_notices_filters_deferred():
    text = ("QA Notice: Pre-stripped files found\n"
            "QA Notice: Unresolved soname dependencies: libfoo.so.1\n"
            "regular output line")
    got = scan_qa_notices(text)
    assert any("Pre-stripped" in n for n in got)
    assert not any("Unresolved soname" in n for n in got)


def test_build_test_surfaces_stderr_qa(tmp_path):
    def fake_run(args, **kw):
        if args[-1] == "install":
            return subprocess.CompletedProcess(
                args, 0, stdout="", stderr="QA Notice: Pre-stripped files found")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(_eb(tmp_path), level="full", runner=fake_run)
    assert res["ok"] is True  # advisory only, verdict unchanged
    assert any("Pre-stripped" in n for n in res["qa_notices"])


# ---- P1-22: pkgcheck --net / --commits + DeadUrl reverify ----
def test_pkgcheck_net_flag(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    run_pkgcheck(tmp_path, net=True, runner=fake_run)
    assert "--net" in seen["args"]


def test_pkgcheck_commits_has_no_path_target(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        seen["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    run_pkgcheck_commits(tmp_path, net=True, runner=fake_run)
    assert "--commits" in seen["args"] and "--net" in seen["args"]
    assert str(tmp_path) not in seen["args"]  # --commits derives targets from git diff
    assert seen["cwd"] == str(tmp_path)


def test_reverify_confirmed_vs_transient():
    results = [
        {"code": "DeadUrl", "msg": "SRC_URI: https://example.com/dead.tar.gz"},
        {"code": "DeadUrl", "msg": "SRC_URI: https://example.com/alive.tar.gz"},
        {"code": "DeadUrl", "msg": "HOMEPAGE: https://example.com/home"},
    ]

    def fake_run(args, **kw):
        url = args[-1]
        return subprocess.CompletedProcess(
            args, 0, stdout=("404" if "dead" in url else "200"), stderr="")

    rv = reverify_url_findings(results, runner=fake_run)
    assert [c["url"] for c in rv["confirmed"]] == ["https://example.com/dead.tar.gz"]
    assert [t["url"] for t in rv["transient"]] == ["https://example.com/alive.tar.gz"]
    # HOMEPAGE finding is ignored (does not block install)
    assert rv["checked"] == 2


# ---- P1-21: truncated-distfile size guard ----
def test_parse_manifest_dist():
    text = ("DIST foo-1.2.3.tar.gz 1048576 BLAKE2B ab SHA512 cd\n"
            "EBUILD foo-1.2.3.ebuild 100 BLAKE2B ee SHA512 ff\n")
    assert parse_manifest_dist(text) == [{"name": "foo-1.2.3.tar.gz", "size": 1048576}]


def test_extract_src_uri_multiline_rename_expand():
    eb = ('SRC_URI="https://example.com/${P}.tar.gz\n'
          '  amd64? ( https://example.com/bin_${PV}.deb -> foo-${PV}-amd64.deb )"\n')
    m = extract_src_uri_map(eb, _pv_subs("foo-1.2.3.ebuild"))
    assert m["foo-1.2.3.tar.gz"] == "https://example.com/foo-1.2.3.tar.gz"
    assert m["foo-1.2.3-amd64.deb"] == "https://example.com/bin_1.2.3.deb"


def test_verify_sizes_flags_truncation():
    manifest = "DIST big.deb 2000000000 BLAKE2B ab SHA512 cd\n"
    src_map = {"big.deb": "https://example.com/big.deb"}

    def fake_run(args, **kw):  # upstream is bigger -> local truncated
        return subprocess.CompletedProcess(args, 0, stdout="2934988000", stderr="")

    res = verify_manifest_sizes(manifest, src_map, runner=fake_run)
    assert res["ok"] is False
    assert res["mismatches"][0]["name"] == "big.deb"
    assert res["remediation"]


def test_verify_sizes_skips_small_and_matching():
    manifest = ("DIST small.tar.gz 1000 BLAKE2B ab SHA512 cd\n"
                "DIST big.deb 2000000000 BLAKE2B ab SHA512 cd\n")
    src_map = {"big.deb": "https://example.com/big.deb"}

    def fake_run(args, **kw):  # big matches; small must never be fetched
        assert "small" not in args[-1]
        return subprocess.CompletedProcess(args, 0, stdout="2000000000", stderr="")

    res = verify_manifest_sizes(manifest, src_map, runner=fake_run)
    assert res["ok"] is True
    assert [c["name"] for c in res["checked"]] == ["big.deb"]


# ---- P2-30: triage skip vs escalate ----
def test_triage_kind_roundtrip_and_filter(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    skip_issue(log, 1, "a/b", "1", "blocked")                       # default skip
    skip_issue(log, 2, "c/d", "2", "needs .cabal", kind="escalate")
    assert [r["issue"] for r in list_skipped(log, kind="escalate")] == [2]
    assert [r["issue"] for r in list_skipped(log, kind="skip")] == [1]


def test_triage_missing_kind_is_skip(tmp_path):
    log = tmp_path / "old.jsonl"
    log.write_text('{"issue":3,"cat_pkg":"e/f","target_version":"3","reason":"x"}\n',
                   encoding="utf-8")
    assert list_skipped(log, kind="skip")[0]["issue"] == 3
    assert list_skipped(log, kind="escalate") == []


def test_cli_triage_escalate_roundtrip(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    r = CliRunner().invoke(cli_mod.cli, [
        "triage", "skip", "55", "--cat-pkg", "dev-haskell/foo",
        "--target-version", "2.0", "--reason", "hackport regen", "--kind", "escalate"])
    assert r.exit_code == 0
    assert json.loads(r.output)["kind"] == "escalate"
    r2 = CliRunner().invoke(cli_mod.cli, ["triage", "list", "--kind", "escalate"])
    assert json.loads(r2.output)[0]["issue"] == 55
