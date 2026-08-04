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

import pytest

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
        issues["totalCount"] = 101
        if len(queries) == 1:
            template = issues["nodes"][0]
            issues["nodes"] = [
                {**template, "number": number,
                 "url": f"https://github.com/Gentoo-zh/gentoo-zh/issues/{number}"}
                for number in range(1, 101)
            ]
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


def _selector_node(number, cat_pkg="app-misc/example", version="2.0",
                   revision="2026-08-04T01:00:00Z", comments=None):
    if comments is None:
        comments = []
    return {
        "number": number,
        "title": f"[nvchecker] {cat_pkg} can be bump to {version}",
        "body": "oldver: 1.0\nCC: @maintainer",
        "state": "OPEN",
        "updatedAt": revision,
        "url": f"https://github.com/gentoo-zh/overlay/issues/{number}",
        "author": {"login": "gentoo-zh-bot"},
        "labels": {
            "totalCount": 1,
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [{"name": "nvchecker"}],
        },
        "comments": {
            "totalCount": len(comments),
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": comments,
        },
    }


def _selector_comment(body, revision="2026-08-04T01:00:00Z",
                      author="gentoo-zh-autobump[bot]", comment_id="IC_1"):
    return {
        "id": comment_id,
        "url": f"https://github.com/gentoo-zh/overlay/issues/1#issuecomment-{comment_id}",
        "author": {"login": author},
        "body": body,
        "createdAt": revision,
        "updatedAt": revision,
    }


def _manual_status(cat_pkg="app-misc/example", version="2.0"):
    return (
        f"**autobump** can't bump `{cat_pkg}` \u2192 `{version}` mechanically: "
        "**payload layout changed**. Needs a manual bump.\n"
        "\u2014 `autobump` enabled\n\n<!-- autobump-status -->"
    )


def _config_loader(content):
    def load(remote_name, path, runner):
        assert remote_name == "canonical"
        assert path == ".github/workflows/overlay.toml"
        return {
            "remote_name": remote_name,
            "remote_url": "git@github.com:gentoo-zh/overlay.git",
            "base_oid": "a" * 40,
            "config_path": path,
            "content": content,
            "fetched": True,
        }
    return load


def _selector_runner(nodes, total_count=None, explicit=None):
    explicit = explicit or {}
    total_count = len(nodes) if total_count is None else total_count

    def fake_run(args, **kwargs):
        if args[:3] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        query = args[4]
        if "issue(number:" in query:
            number = int(query.split("issue(number:", 1)[1].split(")", 1)[0])
            response = {"data": {"repository": {"issue": explicit.get(number)}}}
        else:
            response = {"data": {"repository": {"issues": {
                "totalCount": total_count,
                "pageInfo": {"hasNextPage": total_count > len(nodes),
                             "endCursor": "more" if total_count > len(nodes) else None},
                "nodes": nodes,
            }}}}
        return subprocess.CompletedProcess(args, 0, json.dumps(response), "")
    return fake_run


def _run_selector(selector, config, nodes, **kwargs):
    return run_bump_issues(
        autobump=selector,
        canonical_remote="canonical",
        canonical_loader=_config_loader(config),
        runner=_selector_runner(nodes, kwargs.pop("total_count", None),
                                kwargs.pop("explicit", None)),
        **kwargs,
    )


def test_autobump_off_selects_absent_key_and_records_exact_entry():
    node = _selector_node(1)
    result = _run_selector(
        "off", '["app-misc/example"]\nsource = "github"\n', [node])
    assert result["selected_count"] == 1
    evidence = result["results"][0]["config_evidence"]
    assert evidence["state"] == "off"
    assert evidence["reason"] == "autobump-key-absent"
    assert evidence["entry"] == {"source": "github"}
    assert len(evidence["sha256"]) == 64


def test_autobump_true_and_false_are_typed():
    node = _selector_node(1)
    enabled = _run_selector(
        "on", '["app-misc/example"]\nautobump = true\n', [node])
    disabled = _run_selector(
        "off", '["app-misc/example"]\nautobump = false\n', [node])
    assert enabled["results"][0]["config_evidence"]["state"] == "on"
    assert enabled["results"][0]["selection_reason"] == "selector:autobump-on"
    assert disabled["results"][0]["config_evidence"]["state"] == "off"
    assert disabled["results"][0]["config_evidence"]["autobump"] is False


def test_missing_package_entry_is_unknown_and_not_selected():
    result = _run_selector(
        "off", '["app-misc/other"]\nautobump = false\n',
        [_selector_node(1)])
    assert result["results"] == []
    candidate = result["candidates"][0]
    assert candidate["config_evidence"]["state"] == "unknown"
    assert candidate["selection_reason"] == "selector-evidence-unknown"


def test_manual_required_accepts_only_current_repository_status():
    revision = "2026-08-04T01:00:00Z"
    comment = _selector_comment(_manual_status(), revision=revision)
    result = _run_selector(
        "manual-required", '["app-misc/example"]\nautobump = true\n',
        [_selector_node(1, revision=revision, comments=[comment])])
    item = result["results"][0]
    assert item["status_evidence"]["state"] == "manual-required"
    assert item["status_evidence"]["revision"] == revision
    assert len(item["status_evidence"]["body_sha256"]) == 64
    assert item["selection_reason"] == "selector:manual-required"


def test_manual_required_rejects_stale_status_with_explicit_evidence():
    comment = _selector_comment(
        _manual_status(), revision="2026-08-04T00:59:00Z")
    result = _run_selector(
        "manual-required", '["app-misc/example"]\nautobump = true\n',
        [_selector_node(1, comments=[comment])])
    assert result["results"] == []
    status = result["candidates"][0]["status_evidence"]
    assert status["state"] == "unknown"
    assert status["reason"] == "status-revision-stale"
    assert status["comment_id"] == "IC_1"


def test_manual_required_rejects_lookalike_author_and_body():
    untrusted = _selector_comment(_manual_status(), author="lookalike[bot]")
    malformed = _selector_comment(
        _manual_status().replace("Needs a manual bump.", "manual bump needed."),
        comment_id="IC_2")
    for comment, reason in ((untrusted, "status-author-mismatch"),
                            (malformed, "status-body-not-current-protocol")):
        result = _run_selector(
            "manual-required", '["app-misc/example"]\nautobump = true\n',
            [_selector_node(1, comments=[comment])])
        assert result["results"] == []
        assert result["candidates"][0]["status_evidence"]["reason"] == reason


def test_manual_required_rejects_incomplete_comment_pagination():
    node = _selector_node(1, comments=[_selector_comment(_manual_status())])
    node["comments"]["totalCount"] = 2
    result = _run_selector(
        "manual-required", '["app-misc/example"]\nautobump = true\n', [node])
    assert result["results"] == []
    candidate = result["candidates"][0]
    assert candidate["comments_complete"] is False
    assert candidate["status_evidence"]["reason"] == "comment-count-mismatch"


def test_manual_required_rejects_ambiguous_marked_comments():
    comments = [
        _selector_comment(_manual_status(), comment_id="IC_1"),
        _selector_comment(_manual_status(), comment_id="IC_2"),
    ]
    result = _run_selector(
        "manual-required", '["app-misc/example"]\nautobump = true\n',
        [_selector_node(1, comments=comments)])
    assert result["results"] == []
    status = result["candidates"][0]["status_evidence"]
    assert status["reason"] == "ambiguous-status-comments"
    assert len(status["candidates"]) == 2


def test_explicit_issue_include_mode_unions_it_with_the_filtered_queue():
    queued = _selector_node(1, cat_pkg="app-misc/queued")
    explicit_node = _selector_node(
        2, cat_pkg="app-misc/explicit",
        comments=[_selector_comment("ordinary comment", comment_id="IC_2")])
    config = (
        '["app-misc/queued"]\nautobump = true\n'
        '["app-misc/explicit"]\nautobump = true\n'
    )
    result = _run_selector(
        "off", config, [queued], issues=[2], explicit={2: explicit_node})
    assert result["total_count"] == 1
    assert result["fetched_count"] == 2
    assert result["selected_count"] == 1
    assert result["issue_mode"] == "include"
    assert result["selection_expression"] == {
        "issue_mode": "include",
        "composition": "filtered_queue_or_explicit",
        "queue": {
            "evaluated": True,
            "repository": "gentoo-zh/overlay",
            "label": "nvchecker",
            "state": "open",
            "limit": 100,
            "maintainer": None,
            "package": None,
            "autobump": "off",
        },
        "explicit_issues": [2],
    }
    assert result["resulting_issues"] == [2]
    item = result["results"][0]
    assert item["issue"] == 2
    assert item["comments_complete"] is True
    assert item["comments"][0]["body"] == "ordinary comment"
    assert item["selection_reason"] == "explicit-issue"
    assert result["selected"] == [{
        "issue": 2,
        "cat_pkg": "app-misc/explicit",
        "selection_reason": "explicit-issue",
        "selection_reasons": ["explicit-issue"],
    }]


def test_exact_issue_mode_fetches_and_selects_only_explicit_issues():
    queued = _selector_node(1, cat_pkg="app-misc/queued")
    explicit_nodes = {
        3: _selector_node(3, cat_pkg="app-misc/three"),
        2: _selector_node(2, cat_pkg="app-misc/two"),
    }
    result = _run_selector(
        "any", "", [queued], issues=[3, 2], issue_mode="exact",
        explicit=explicit_nodes)

    assert result["ok"] is True
    assert result["total_count"] == 0
    assert result["fetched_count"] == 2
    assert result["truncated"] is False
    assert result["resulting_issues"] == [3, 2]
    assert [item["issue"] for item in result["candidates"]] == [3, 2]
    assert result["selection_expression"]["composition"] == "explicit_only"


def test_exact_issue_mode_requires_an_explicit_issue():
    result = run_bump_issues(
        issue_mode="exact", runner=lambda *_args, **_kwargs: None)
    assert result["ok"] is False
    assert "requires at least one --issue" in result["error"]


def test_explicit_issue_fails_closed_on_incomplete_comments():
    explicit_node = _selector_node(
        2, comments=[_selector_comment("ordinary comment", comment_id="IC_2")])
    explicit_node["comments"]["totalCount"] = 2
    result = _run_selector(
        "any", '["app-misc/example"]\nautobump = true\n', [],
        issues=[2], explicit={2: explicit_node})
    assert result["ok"] is False
    assert "comments are incomplete" in result["error"]


def test_explicit_issue_requires_complete_nvchecker_label_evidence():
    missing_label = _selector_node(2)
    missing_label["labels"] = {
        "totalCount": 1,
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"name": "unrelated"}],
    }
    result = _run_selector(
        "any", '["app-misc/example"]\nautobump = true\n', [],
        issues=[2], explicit={2: missing_label})
    assert result["ok"] is False
    assert "does not have the nvchecker label" in result["error"]

    truncated = _selector_node(2)
    truncated["labels"]["totalCount"] = 2
    result = _run_selector(
        "any", '["app-misc/example"]\nautobump = true\n', [],
        issues=[2], explicit={2: truncated})
    assert result["ok"] is False
    assert "label evidence is incomplete" in result["error"]


def test_non_any_selector_requires_complete_canonical_evidence():
    result = run_bump_issues(
        autobump="on", runner=_selector_runner([_selector_node(1)]))
    assert result["ok"] is False
    assert "explicit canonical remote" in result["error"]


def test_snapshot_reconstruction_uses_stored_selection_without_body_parsing():
    result = _run_selector(
        "on", '["app-misc/example"]\nautobump = true\n',
        [_selector_node(1)])
    result.pop("exit_code")
    from gzh.bump_issues import reconstruct_selected_results
    reconstructed = reconstruct_selected_results(result)
    assert reconstructed == result["results"]
    reconstructed[0]["body"] = "changed"
    assert result["results"][0]["body"] != "changed"

    broken = json.loads(json.dumps(result))
    broken["selected_count"] = 0
    try:
        reconstruct_selected_results(broken)
    except ValueError as exc:
        assert "counts" in str(exc)
    else:
        raise AssertionError("expected inconsistent snapshot to fail")

    broken = json.loads(json.dumps(result))
    broken["resulting_issues"] = []
    with pytest.raises(ValueError, match="resulting issue set"):
        reconstruct_selected_results(broken)


def test_graphql_queries_request_stable_comment_evidence():
    query = build_query("gentoo-zh", "overlay", "OPEN", 10, True)
    assert "id url author{login} body createdAt updatedAt" in query
    assert "labels(first:100){totalCount pageInfo{hasNextPage endCursor}" in query


def test_default_config_loader_reads_the_explicit_fetched_oid():
    from gzh.bump_issues import load_canonical_config
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args[:3] == ["git", "remote", "get-url"]:
            stdout = "git@github.com:gentoo-zh/overlay.git\n"
        elif args[:3] == ["git", "fetch", "--quiet"]:
            stdout = ""
        elif args[:3] == ["git", "rev-parse", "--verify"]:
            stdout = "b" * 40 + "\n"
        elif args[:2] == ["git", "show"]:
            stdout = b'["app-misc/example"]\nautobump = true\n'
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, stdout, "")

    loaded = load_canonical_config("canonical", runner=fake_run)
    assert loaded["base_oid"] == "b" * 40
    assert loaded["content"].endswith(b"autobump = true\n")
    assert calls[1][0] == [
        "git", "fetch", "--quiet", "canonical",
        "+refs/heads/master:refs/remotes/canonical/master",
    ]
    assert calls[-1][0] == [
        "git", "show",
        f"{'b' * 40}:.github/workflows/overlay.toml",
    ]


def test_config_loader_binds_every_git_query_to_the_overlay_root(tmp_path):
    from gzh.bump_issues import load_canonical_config
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        operation = args[3]
        if operation == "remote":
            stdout = "git@github.com:gentoo-zh/overlay.git\n"
        elif operation == "fetch":
            stdout = ""
        elif operation == "rev-parse":
            stdout = "b" * 40 + "\n"
        elif operation == "show":
            stdout = b'["app-misc/example"]\nautobump = true\n'
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, stdout, "")

    load_canonical_config("canonical", runner=fake_run, cwd=tmp_path)

    assert all(command[:3] == ["git", "-C", str(tmp_path)]
               for command in calls)


def test_config_loader_rejects_noncanonical_url_before_fetch():
    from gzh.bump_issues import load_canonical_config
    calls = []

    def fake_run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 0, "git@github.com:someone/fork.git\n", "")

    with pytest.raises(RuntimeError, match="does not match"):
        load_canonical_config(
            "upstream", runner=fake_run,
            expected_repository="gentoo-zh/overlay")

    assert calls == [["git", "remote", "get-url", "upstream"]]
