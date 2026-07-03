from gzh.bump_issues import parse_body, parse_title


def test_parse_title_typical():
    assert parse_title("[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40") == \
        ("media-fonts/sarasa-gothic", "1.0.40")


def test_parse_title_bin_and_version_suffix():
    assert parse_title("[nvchecker] net-proxy/naiveproxy-bin can be bump to 150.0.7871.63_p1") == \
        ("net-proxy/naiveproxy-bin", "150.0.7871.63_p1")


def test_parse_title_unmatched_returns_none():
    assert parse_title("some other title") is None
    assert parse_title("[nvchecker] not the bump pattern") is None


def test_parse_body_fields():
    assert parse_body("oldver: 1.0.39\nCC: @Linerre") == \
        {"oldver": "1.0.39", "maintainer": "Linerre"}


def test_parse_body_missing_fields():
    assert parse_body("nothing useful here") == \
        {"oldver": None, "maintainer": None}


def test_parse_body_cc_without_at_sign():
    assert parse_body("CC: someone")["maintainer"] == "someone"


from gzh.bump_issues import apply_filters, graphql_to_queue

NODES = [
    {"number": 10581,
     "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
     "body": "oldver: 1.0.39\nCC: @Linerre", "state": "OPEN",
     "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
     "comments": {"nodes": [
         {"author": {"login": "microcai"}, "body": "hi",
          "createdAt": "2026-07-01T00:00:00Z"}]}},
    {"number": 999, "title": "random nvchecker note", "body": "",
     "state": "OPEN", "url": "u", "comments": {"nodes": []}},
]


def test_graphql_to_queue_skips_unmatched():
    queue, skipped = graphql_to_queue(NODES)
    assert skipped == 1
    assert len(queue) == 1
    item = queue[0]
    assert item["issue"] == 10581
    assert item["cat_pkg"] == "media-fonts/sarasa-gothic"
    assert item["target_version"] == "1.0.40"
    assert item["oldver"] == "1.0.39"
    assert item["maintainer"] == "Linerre"
    assert item["state"] == "open"
    assert item["url"].endswith("/issues/10581")
    assert item["comments_truncated"] is False
    assert item["comments"][0]["author"] == "microcai"


def test_graphql_to_queue_no_comments_option():
    queue, _ = graphql_to_queue(NODES, with_comments=False)
    assert queue[0]["comments"] == []
    assert queue[0]["comments_truncated"] is False


def test_graphql_to_queue_truncates_over_50_comments():
    many = [{"author": {"login": "x"}, "body": "y", "createdAt": "z"}] * 51
    nodes = [{"number": 1, "title": "[nvchecker] a/b can be bump to 1",
              "body": "", "state": "OPEN", "url": "u", "comments": {"nodes": many}}]
    queue, _ = graphql_to_queue(nodes)
    assert len(queue[0]["comments"]) == 50
    assert queue[0]["comments_truncated"] is True


def test_apply_filters_by_maintainer_and_pkg():
    queue, _ = graphql_to_queue(NODES)
    assert len(apply_filters(queue, maintainer="Linerre")) == 1
    assert apply_filters(queue, maintainer="nobody") == []
    assert len(apply_filters(queue, pkg="media-fonts/sarasa-gothic")) == 1
    assert apply_filters(queue, pkg="x/y") == []


import json
import subprocess

from gzh.bump_issues import build_query, run_bump_issues


def test_build_query_open_with_comments():
    q = build_query("Gentoo-zh", "gentoo-zh", "OPEN", 200, True)
    assert 'owner:"Gentoo-zh"' in q
    assert 'name:"gentoo-zh"' in q
    assert 'labels:["nvchecker"]' in q
    assert "states:[OPEN]" in q
    assert "first:200" in q
    assert "comments(first:50)" in q


def test_build_query_all_state_no_comments():
    q = build_query("Gentoo-zh", "gentoo-zh", None, 50, False)
    assert "states:" not in q
    assert "comments" not in q


def _resp():
    return {"data": {"repository": {"issues": {"nodes": [
        {"number": 10581,
         "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
         "body": "oldver: 1.0.39\nCC: @Linerre", "state": "OPEN",
         "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
         "comments": {"nodes": []}},
    ]}}}}


def test_run_bump_issues_success():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(_resp()), "")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is True
    assert res["results"][0]["cat_pkg"] == "media-fonts/sarasa-gothic"
    assert res["skipped"] == 0
    assert res["exit_code"] == 0


def test_run_bump_issues_not_authenticated():
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "not logged in")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 2


def test_run_bump_issues_gh_failure_after_auth():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "rate limited")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "rate limited" in res["stderr"]


def test_run_bump_issues_filters_pass_through():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(_resp()), "")
    res = run_bump_issues(maintainer="nobody", runner=fake_run)
    assert res["ok"] is True
    assert res["results"] == []


from click.testing import CliRunner

from gzh.cli import cli


def test_bump_issues_help_registered():
    result = CliRunner().invoke(cli, ["bump-issues", "--help"])
    assert result.exit_code == 0
    assert "nvchecker" in result.output.lower() or "bump" in result.output.lower()


def test_bump_issues_not_authenticated_exits_2(monkeypatch):
    import gzh.cli as cli_mod

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "not logged in")
    monkeypatch.setattr("gzh.bump_issues.subprocess.run", fake_run)
    result = CliRunner().invoke(cli_mod.cli, ["bump-issues"])
    assert result.exit_code == 2


def test_run_bump_issues_caps_limit_at_100():
    captured = {}

    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        captured["query"] = args[4]
        return subprocess.CompletedProcess(args, 0, json.dumps(_resp()), "")

    res = run_bump_issues(limit=250, runner=fake_run)
    assert res["ok"] is True
    assert "first:100" in captured["query"]
    assert "first:250" not in captured["query"]
