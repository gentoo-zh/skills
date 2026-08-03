"""Regression tests for cross-module maintenance safeguards."""
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


TRIAGE_UPDATED = "2026-08-01T12:00:00Z"


def _skip(log, issue, cat_pkg, version, reason, *, kind="skip"):
    return skip_issue(
        log, issue, cat_pkg, version, reason, kind=kind,
        issue_updated_at=TRIAGE_UPDATED, expected_event_id="none")


def _eb(tmp_path):
    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    return eb


def test_commit_adds_signoff(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
               message="cat/foo: add 1", runner=fake_run)
    assert "--signoff=true" in seen["args"]
    assert "--gpg-sign" not in seen["args"]  # not forced


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


def test_pkgcheck_net_flag(tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

    run_pkgcheck(tmp_path, net=True, runner=fake_run)
    assert "--net" in seen["args"]


def _git_faker(seen, *, merge_base="abc123", commit_count="1",
               remote_url="git@github.com:gentoo-zh/overlay.git"):
    """Fake runner answering the git probes run_pkgcheck_commits makes before scanning."""
    def fake_run(args, **kw):
        if args[:2] == ["git", "remote"] and args[2] == "-v":
            return subprocess.CompletedProcess(args, 0, stdout=f"upstream\t{remote_url} (fetch)\n", stderr="")
        if args[:2] == ["git", "symbolic-ref"]:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if args[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(args, 0 if merge_base else 1, stdout=merge_base, stderr="")
        if args[:3] == ["git", "rev-list", "--count"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{commit_count}\n", stderr="")
        seen["args"] = args
        seen["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    return fake_run


def test_pkgcheck_commits_scopes_to_the_branch(tmp_path):
    """A bare --commits compares against a fork's lagging origin, so the range and the
    canonical remote both have to be explicit (overlay AGENTS.md, Commands and QA)."""
    seen = {}
    run_pkgcheck_commits(tmp_path, net=True, runner=_git_faker(seen))
    args = seen["args"]
    assert args[args.index("--git-remote") + 1] == "upstream"
    assert "--commits=abc123..HEAD" in args
    assert "--net" in args
    assert str(tmp_path) not in args  # the range selects the targets, not a path
    assert seen["cwd"] == str(tmp_path)


def test_pkgcheck_commits_refuses_an_empty_merge_base(tmp_path):
    """git reads `..HEAD` as `HEAD..HEAD`, so pkgcheck would scan nothing and exit 0."""
    seen = {}
    try:
        run_pkgcheck_commits(tmp_path, net=True, runner=_git_faker(seen, merge_base=""))
    except RuntimeError as exc:
        assert "merge-base" in str(exc)
    else:
        raise AssertionError("expected RuntimeError instead of a silently green gate")
    assert "args" not in seen  # never reached pkgcheck


def test_pkgcheck_commits_refuses_an_empty_commit_range(tmp_path):
    seen = {}
    try:
        run_pkgcheck_commits(
            tmp_path, net=True, runner=_git_faker(seen, commit_count="0"))
    except RuntimeError as exc:
        assert "empty range" in str(exc)
    else:
        raise AssertionError("expected RuntimeError instead of a zero-target gate")
    assert "args" not in seen


def test_pkgcheck_commits_rejects_explicit_personal_remote(tmp_path):
    def fake_run(args, **kw):
        if args[:3] == ["git", "remote", "get-url"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="git@github.com:someone/overlay.git\n", stderr="")
        raise AssertionError(f"should not run {args}")

    try:
        run_pkgcheck_commits(tmp_path, remote="origin", runner=fake_run)
    except RuntimeError as exc:
        assert "does not point" in str(exc)
    else:
        raise AssertionError("expected an explicit personal remote to be rejected")


def test_pkgcheck_commits_needs_an_unambiguous_canonical_remote(tmp_path):
    def fake_run(args, **kw):
        if args[:3] == ["git", "remote", "-v"]:
            return subprocess.CompletedProcess(args, 0, stdout="origin\tgit@github.com:someone/other.git (fetch)\n", stderr="")
        raise AssertionError(f"should not run {args}")
    try:
        run_pkgcheck_commits(tmp_path, net=True, runner=fake_run)
    except RuntimeError as exc:
        assert "gentoo-zh/overlay" in str(exc)
    else:
        raise AssertionError("expected RuntimeError when no canonical remote matches")


def test_reverify_confirmed_vs_transient():
    results = [
        {"code": "DeadUrl", "msg": "SRC_URI: https://example.com/dead.tar.gz"},
        {"code": "DeadUrl", "msg": "SRC_URI: https://example.com/alive.tar.gz"},
        {"code": "RedirectedUrl", "msg": "SRC_URI: https://example.com/moved.tar.gz"},
        {"code": "DeadUrl", "msg": "HOMEPAGE: https://example.com/home"},
    ]

    def fake_run(args, **kw):
        url = args[-1]
        return subprocess.CompletedProcess(
            args, 0, stdout=("404" if "dead" in url else "200"), stderr="")

    rv = reverify_url_findings(results, runner=fake_run)
    assert [c["url"] for c in rv["confirmed"]] == ["https://example.com/dead.tar.gz"]
    assert [t["url"] for t in rv["transient"]] == ["https://example.com/alive.tar.gz"]
    assert [r["url"] for r in rv["redirected"]] == ["https://example.com/moved.tar.gz"]
    # HOMEPAGE finding is ignored (does not block install)
    assert rv["checked"] == 3


def test_reverify_403_429_go_to_needs_human():
    # auth-gated / rate-limited codes are inconclusive, not "dead" (would wrongly block)
    results = [{"__class__": "DeadUrl", "msg": "SRC_URI: https://example.com/gated.tar.gz"}]

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout="403", stderr="")

    rv = reverify_url_findings(results, runner=fake_run)
    assert rv["confirmed"] == [] and rv["transient"] == []
    assert rv["needs_human"][0]["url"] == "https://example.com/gated.tar.gz"


def test_reverify_transport_failure_requires_human_review():
    results = [{"__class__": "DeadUrl",
                "msg": "SRC_URI: https://example.com/partial.tar.gz"}]

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 18, stdout="200", stderr="partial")

    rv = reverify_url_findings(results, runner=fake_run)
    assert rv["transient"] == []
    assert rv["needs_human"] == [{
        "url": "https://example.com/partial.tar.gz",
        "http_code": "200",
        "finding": "DeadUrl",
        "transport_returncode": 18,
    }]


def test_pkgcheck_commits_cli_fails_on_inconclusive_url(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_mod, "run_pkgcheck_commits",
        lambda *args, **kwargs: {"ok": True, "results": [], "raw_returncode": 0})
    monkeypatch.setattr(
        cli_mod, "reverify_url_findings",
        lambda results: {"confirmed": [], "redirected": [], "transient": [],
                         "needs_human": [{"http_code": "429"}], "checked": 1})
    result = CliRunner().invoke(cli_mod.cli, ["pkgcheck-commits"])
    assert result.exit_code == 1


def test_pkgcheck_commits_cli_cannot_pass_without_url_recheck(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda: tmp_path)
    monkeypatch.setattr(
        cli_mod, "run_pkgcheck_commits",
        lambda *args, **kwargs: {"ok": True, "results": [], "raw_returncode": 0})
    result = CliRunner().invoke(
        cli_mod.cli, ["pkgcheck-commits", "--no-reverify"])
    assert result.exit_code == 1
    assert '"skipped": true' in result.output


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


def test_triage_kind_roundtrip_and_filter(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    _skip(log, 1, "a/b", "1", "blocked")
    _skip(log, 2, "c/d", "2", "needs .cabal", kind="escalate")
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
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        cli_mod, "get_issue_updated_at", lambda repo, issue: TRIAGE_UPDATED)
    r = CliRunner().invoke(cli_mod.cli, [
        "triage", "skip", "55", "--cat-pkg", "dev-haskell/foo",
        "--target-version", "2.0", "--issue-updated-at", TRIAGE_UPDATED,
        "--expected-event-id", "none", "--reason", "hackport regen",
        "--kind", "escalate"])
    assert r.exit_code == 0
    assert json.loads(r.output)["kind"] == "escalate"
    r2 = CliRunner().invoke(cli_mod.cli, ["triage", "list", "--kind", "escalate"])
    assert json.loads(r2.output)[0]["issue"] == 55
