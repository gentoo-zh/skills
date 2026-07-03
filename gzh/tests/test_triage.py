from gzh.triage import list_skipped, skip_issue


def test_list_empty_when_no_file(tmp_path):
    assert list_skipped(tmp_path / "skip-log.jsonl") == []


def test_skip_appends_and_list_roundtrip(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    rec = skip_issue(log, 10588, "net-proxy/v2rayA", "2.4.6", "crash")
    assert rec["issue"] == 10588
    assert rec["cat_pkg"] == "net-proxy/v2rayA"
    assert rec["target_version"] == "2.4.6"
    assert rec["reason"] == "crash"
    assert rec["skipped_at"]  # non-empty ISO timestamp
    listed = list_skipped(log)
    assert len(listed) == 1
    assert listed[0]["issue"] == 10588


def test_list_filter_by_pkg(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    skip_issue(log, 1, "a/b", "1", "r1")
    skip_issue(log, 2, "c/d", "2", "r2")
    assert len(list_skipped(log, pkg="a/b")) == 1
    assert list_skipped(log, pkg="a/b")[0]["issue"] == 1


def test_list_ignores_blank_comment_bad_lines(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    log.write_text("# header comment\n{bad json\n\n", encoding="utf-8")
    skip_issue(log, 3, "e/f", "3", "r3")
    listed = list_skipped(log)
    assert len(listed) == 1
    assert listed[0]["issue"] == 3


def test_skip_creates_parent_dir(tmp_path):
    log = tmp_path / "sub" / "nested" / "skip-log.jsonl"
    skip_issue(log, 9, "x/y", "1", "r")
    assert log.exists()
    assert len(list_skipped(log)) == 1


def test_list_skips_non_dict_json_lines(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    log.write_text("42\n[1,2]\n\"str\"\n", encoding="utf-8")
    skip_issue(log, 5, "a/b", "1", "r")
    listed = list_skipped(log)
    assert len(listed) == 1
    assert listed[0]["issue"] == 5


from click.testing import CliRunner

from gzh.cli import cli


def test_triage_list_help_registered():
    result = CliRunner().invoke(cli, ["triage", "list", "--help"])
    assert result.exit_code == 0


def test_triage_skip_and_list_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    r1 = CliRunner().invoke(cli_mod.cli,
                            ["triage", "skip", "100",
                             "--cat-pkg", "a/b", "--target-version", "1.0",
                             "--reason", "testing"])
    assert r1.exit_code == 0
    import json as _json
    assert _json.loads(r1.output)["issue"] == 100
    r2 = CliRunner().invoke(cli_mod.cli, ["triage", "list"])
    assert r2.exit_code == 0
    listed = _json.loads(r2.output)
    assert len(listed) == 1
    assert listed[0]["cat_pkg"] == "a/b"
    # file landed under <root>/triage/skip-log.jsonl
    assert (tmp_path / "triage" / "skip-log.jsonl").exists()
