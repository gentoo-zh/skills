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
     "updatedAt": "2026-07-02T00:00:00Z",
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
    assert item["body"] == "oldver: 1.0.39\nCC: @Linerre"
    assert item["state"] == "open"
    assert item["updated_at"] == "2026-07-02T00:00:00Z"
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
              "body": "", "state": "OPEN", "url": "u",
              "comments": {"nodes": many, "totalCount": 52}}]
    queue, _ = graphql_to_queue(nodes)
    assert len(queue[0]["comments"]) == 51
    assert queue[0]["comments_truncated"] is True


def test_apply_filters_by_maintainer_and_pkg():
    queue, _ = graphql_to_queue(NODES)
    assert len(apply_filters(queue, maintainer="Linerre")) == 1
    assert apply_filters(queue, maintainer="nobody") == []
    assert len(apply_filters(queue, pkg="media-fonts/sarasa-gothic")) == 1
    assert apply_filters(queue, pkg="x/y") == []


import json
import subprocess

from gzh.bump_issues import build_query, get_issue_updated_at, run_bump_issues


def test_build_query_open_with_comments():
    q = build_query("Gentoo-zh", "gentoo-zh", "OPEN", 200, True)
    assert 'owner:"Gentoo-zh"' in q
    assert 'name:"gentoo-zh"' in q
    assert 'labels:["nvchecker"]' in q
    assert "states:[OPEN]" in q
    assert "first:100" in q
    assert "comments(first:100)" in q


def test_build_query_all_state_no_comments():
    q = build_query("Gentoo-zh", "gentoo-zh", None, 50, False)
    assert "states:" not in q
    assert "comments" not in q


def _resp():
    return {"data": {"repository": {"issues": {
        "totalCount": 1,
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [
        {"number": 10581,
         "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
         "body": "oldver: 1.0.39\nCC: @Linerre", "state": "OPEN",
         "updatedAt": "2026-07-02T00:00:00Z",
         "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
         "comments": {"totalCount": 0,
                      "pageInfo": {"hasNextPage": False, "endCursor": None},
                      "nodes": []}},
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
    assert res["total_count"] == 1
    assert res["fetched_count"] == 1
    assert res["selected_count"] == 1
    assert res["truncated"] is False
    assert res["exit_code"] == 0


def test_get_issue_updated_at_uses_exact_repository_and_issue():
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(
            args, 0, "2026-08-04T01:02:03Z\n", "")

    assert get_issue_updated_at(
        "gentoo-zh/overlay", 10581, runner=fake_run
    ) == "2026-08-04T01:02:03Z"
    assert seen["args"] == [
        "gh", "api", "repos/gentoo-zh/overlay/issues/10581",
        "--jq", ".updated_at"]


def test_get_issue_updated_at_fails_closed():
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "rate limited")

    try:
        get_issue_updated_at("gentoo-zh/overlay", 10581, runner=fake_run)
    except RuntimeError as exc:
        assert "rate limited" in str(exc)
    else:
        raise AssertionError("expected current issue revision lookup to fail")


def test_run_bump_issues_not_authenticated():
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "not logged in")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 2


def test_run_bump_issues_gh_not_installed():
    def fake_run(args, **kw):
        raise FileNotFoundError("gh not installed")
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


def test_run_bump_issues_graphql_errors():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        body = json.dumps({"errors": [{"message": "rate limit"}]})
        return subprocess.CompletedProcess(args, 0, body, "")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "rate limit" in res["error"]


def test_run_bump_issues_invalid_repo():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(_resp()), "")
    res = run_bump_issues(repo="not-a-repo", runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "invalid --repo" in res["error"]


def test_run_bump_issues_rejects_graphql_injection_in_repo():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError("GraphQL must not run for an invalid repository")

    res = run_bump_issues(repo='owner/name") { viewer { login } }', runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 1


def test_run_bump_issues_invalid_json():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "not-json-at-all", "")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "invalid JSON" in res["error"]


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


def test_run_bump_issues_paginates_to_requested_limit():
    queries = []

    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        query = args[4]
        queries.append(query)
        response = _resp()
        issues = response["data"]["repository"]["issues"]
        if len(queries) == 1:
            issues["nodes"] = issues["nodes"] * 100
            issues["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
        return subprocess.CompletedProcess(args, 0, json.dumps(response), "")

    res = run_bump_issues(limit=101, with_comments=False, runner=fake_run)
    assert res["ok"] is True
    assert len(res["results"]) == 101
    assert "first:100" in queries[0]
    assert "first:1" in queries[1]
    assert 'after:"next"' in queries[1]


def test_run_bump_issues_reports_truncated_queue():
    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        response = _resp()
        issues = response["data"]["repository"]["issues"]
        issues["totalCount"] = 2
        issues["pageInfo"] = {"hasNextPage": True, "endCursor": "next"}
        return subprocess.CompletedProcess(args, 0, json.dumps(response), "")

    result = run_bump_issues(limit=1, with_comments=False, runner=fake_run)
    assert result["ok"] is True
    assert result["fetched_count"] == 1
    assert result["total_count"] == 2
    assert result["truncated"] is True


def test_run_bump_issues_fetches_all_comment_pages():
    calls = []

    def fake_run(args, **kw):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        query = args[4]
        calls.append(query)
        if "issue(number:" in query:
            response = {"data": {"repository": {"issue": {"comments": {
                "totalCount": 2,
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"author": {"login": "b"}, "body": "second",
                           "createdAt": "2026-01-02T00:00:00Z"}],
            }}}}}
        else:
            response = _resp()
            comments = response["data"]["repository"]["issues"]["nodes"][0]["comments"]
            comments.update({
                "totalCount": 2,
                "pageInfo": {"hasNextPage": True, "endCursor": "comments-next"},
                "nodes": [{"author": {"login": "a"}, "body": "first",
                           "createdAt": "2026-01-01T00:00:00Z"}],
            })
        return subprocess.CompletedProcess(args, 0, json.dumps(response), "")

    result = run_bump_issues(runner=fake_run)
    assert result["ok"] is True
    assert [comment["body"] for comment in result["results"][0]["comments"]] == [
        "first", "second"]
    assert result["results"][0]["comments_truncated"] is False
    assert any('after:"comments-next"' in query for query in calls)


def test_write_output_creates_timestamped_json(tmp_path):
    from gzh.bump_issues import write_output
    payload = {"ok": True, "results": [], "skipped": 0}
    p = write_output(payload, tmp_path, "20260704-020000")
    assert p.name == "bump-issues-20260704-020000.json"
    assert p.parent == tmp_path
    written = json.loads(p.read_text())
    assert written["ok"] is True
    assert written["results"] == []


def test_write_output_does_not_overwrite_same_second_snapshot(tmp_path):
    from gzh.bump_issues import write_output
    first = write_output({"sequence": 1}, tmp_path, "20260704-020000")
    second = write_output({"sequence": 2}, tmp_path, "20260704-020000")
    assert first.name == "bump-issues-20260704-020000.json"
    assert second.name == "bump-issues-20260704-020000-1.json"
    assert json.loads(first.read_text())["sequence"] == 1
    assert json.loads(second.read_text())["sequence"] == 2


def test_cli_writes_output_file_and_stdout(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "gzh.cli.run_bump_issues",
        lambda **kw: {"ok": True, "results": [], "skipped": 0, "exit_code": 0})
    result = CliRunner().invoke(cli_mod.cli, ["bump-issues"])
    assert result.exit_code == 0
    files = sorted((tmp_path / "queues").glob("bump-issues-*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["ok"] is True
    assert '"results"' in result.output  # stdout still has full JSON


def test_cli_no_output_skips_file(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setenv("GZH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        "gzh.cli.run_bump_issues",
        lambda **kw: {"ok": True, "results": [], "skipped": 0, "exit_code": 0})
    CliRunner().invoke(cli_mod.cli, ["bump-issues", "--no-output"])
    assert not (tmp_path / "queues").exists()
