from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
COLLECTOR_PATH = (
    ROOT / ".agents" / "skills" / "gzh-maintain-skills" / "scripts"
    / "qa_style_collector.py")
EVIDENCE_STORE_PATH = (
    ROOT / ".agents" / "skills" / "gzh-maintain-skills" / "scripts"
    / "evidence_store.py")
CANONICAL_URL = "https://example.com/overlay.git"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "qa_style_collector_test", COLLECTOR_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


collector = load_module()


def load_evidence_store():
    spec = importlib.util.spec_from_file_location(
        "qa_style_evidence_store_test", EVIDENCE_STORE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


evidence = load_evidence_store()


def git(repository: Path, *arguments: str, env: dict | None = None) -> str:
    proc = subprocess.run(
        ["git", *arguments], cwd=repository, check=True, capture_output=True,
        text=True, env=env)
    return proc.stdout.strip()


def make_repository(path: Path, commits: int = 1) -> Path:
    path.mkdir()
    git(path, "init", "--quiet", "--initial-branch=main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "remote", "add", "origin", CANONICAL_URL)
    (path / "profiles").mkdir()
    (path / "metadata").mkdir()
    (path / "profiles" / "repo_name").write_text(
        "fixture-overlay\n", encoding="utf-8")
    (path / "metadata" / "layout.conf").write_text(
        "masters = gentoo\nthin-manifests = true\n", encoding="utf-8")
    for number in range(commits):
        target = path / f"qa-{number}.txt"
        target.write_text(f"check {number}\n", encoding="utf-8")
        git(path, "add", ".")
        timestamp = f"2025-01-{number + 1:02d}T00:00:00+00:00"
        environment = {
            **os.environ,
            "GIT_AUTHOR_DATE": timestamp,
            "GIT_COMMITTER_DATE": timestamp,
        }
        git(path, "commit", "--quiet", "-m", f"qa: add check {number}",
            env=environment)
    return path


def test_topic_routing_is_ordered():
    topics = collector.route_topics(
        "cat/pkg: fix test install QA",
        ["cat/pkg/Manifest", "cat/pkg/files/fix-license.patch"])

    assert topics == ["qa", "manifest", "license", "patch", "test", "install"]


def test_local_collection_validates_identity_and_normalizes_candidates(tmp_path):
    repository = make_repository(tmp_path / "overlay")
    metadata = repository / "cat" / "pkg" / "metadata.xml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("<pkgmetadata/>\n", encoding="utf-8")
    manifest = metadata.parent / "Manifest"
    manifest.write_text("DIST source.tar 1 SHA512 dead\n", encoding="utf-8")
    git(repository, "add", "cat/pkg/metadata.xml", "cat/pkg/Manifest")
    git(repository, "commit", "--quiet", "-m", "cat/pkg: update metadata")
    status_before = git(repository, "status", "--porcelain=v1")
    root_revision = git(repository, "rev-list", "--max-parents=0", "main")

    report = collector.collect_local(
        repository, "main", limit=2, audit_sources=False, workers=1,
        generated_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
        after_revision=root_revision, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["history_complete"] is True
    assert report["primary_validation_complete"] is False
    assert report["truncated"] is False
    assert report["generated_at"] == "2025-02-01T00:00:00Z"
    assert report["scope"]["repo_name"] == "fixture-overlay"
    assert report["scope"]["layout"] == {
        "masters": "gentoo", "thin-manifests": "true"}
    assert report["scope"]["configured_origin"] == (
        "https://example.com/overlay.git")
    assert report["scope"]["canonical_origin"] == CANONICAL_URL
    assert report["scope"]["canonical_origin_state"] == "verified"
    assert report["scope"]["adapter_id"] == "fixture"
    assert report["scope"]["canonical_repository"] == "example/overlay"
    assert report["scope"]["observed_revision"] == (
        report["scope"]["resolved_ref"])
    current = [candidate for candidate in report["candidates"]
               if candidate["source_revision"] == report["scope"]["resolved_ref"]]
    assert [candidate["topic"] for candidate in current] == [
        "metadata", "manifest"]
    assert all(candidate["scope"] == "example/overlay" for candidate in current)
    assert all(candidate["adapter_id"] == "fixture" for candidate in current)
    assert all(candidate["authority"] == "candidate-history"
               for candidate in current)
    assert current[0]["provenance"]["files"] == [
        "cat/pkg/Manifest", "cat/pkg/metadata.xml"]
    assert current[0]["provenance"]["stat"]["files_changed"] == 2
    cursor = report["source_records"][0]
    assert cursor["id"] == "scope-cursor"
    assert cursor["source_id"] == "scope-cursor"
    assert cursor["role"] == "cursor"
    assert cursor["topics"] == []
    assert cursor["revision"] == report["scope"]["resolved_ref"]
    assert cursor["adapter_id"] == "fixture"
    assert cursor["canonical_repository"] == "example/overlay"
    candidate_sources = [
        source for source in report["source_records"]
        if source.get("role") == "candidate"]
    assert len(candidate_sources) == 1
    assert candidate_sources[0]["topics"] == ["metadata", "manifest"]
    for candidate in current:
        assert any(
            source["id"] == candidate["source_id"]
            and source["source_id"] == candidate["source_id"]
            and source["url"] == candidate["source_url"]
            and source["revision"] == candidate["source_revision"]
            for source in candidate_sources)
    targets = [record for record in report["source_records"]
               if record.get("role") == "primary-validation"]
    assert targets
    assert all(target["state"] == "validation-target" for target in targets)
    assert all(target["validated"] is False for target in targets)
    assert all(set(("id", "authority", "url", "revision", "state"))
               <= set(target) for target in report["source_records"])
    assert git(repository, "status", "--porcelain=v1") == status_before


def test_local_collection_matches_upstream_only_remote(tmp_path):
    repository = make_repository(tmp_path / "overlay")
    git(repository, "remote", "rename", "origin", "upstream")
    configured_url = "git@example.com:overlay.git"
    git(repository, "remote", "set-url", "upstream", configured_url)
    root_revision = git(repository, "rev-parse", "main")
    (repository / "qa-new.txt").write_text("check\n", encoding="utf-8")
    git(repository, "add", "qa-new.txt")
    git(repository, "commit", "--quiet", "-m", "qa: add another check")

    report = collector.collect_local(
        repository, "main", limit=1, audit_sources=False, workers=1,
        after_revision=root_revision, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    assert report["errors"] == []
    assert report["history_complete"] is True
    assert report["scope"]["configured_origin"] == configured_url
    assert report["scope"]["canonical_origin"] == CANONICAL_URL
    assert report["scope"]["canonical_origin_state"] == "verified"
    cursor = next(source for source in report["source_records"]
                  if source["role"] == "cursor")
    candidate = next(source for source in report["source_records"]
                     if source["role"] == "candidate")
    assert cursor["url"] == CANONICAL_URL
    assert candidate["url"] == CANONICAL_URL
    assert report["candidates"][0]["source_url"] == CANONICAL_URL


def test_local_collection_rejects_ambiguous_canonical_remotes(tmp_path):
    repository = make_repository(tmp_path / "overlay")
    git(repository, "remote", "add", "upstream", CANONICAL_URL)

    report = collector.collect_local(
        repository, "main", limit=1, audit_sources=False, workers=1,
        adapter_id="fixture", canonical_repository="example/overlay",
        canonical_url=CANONICAL_URL)

    assert report["ok"] is False
    assert report["errors"][0]["stage"] == "local-repository"
    assert "multiple Git remotes match" in report["errors"][0]["message"]


def test_local_collection_rejects_missing_canonical_remote(tmp_path):
    repository = make_repository(tmp_path / "overlay")

    report = collector.collect_local(
        repository, "main", limit=1, audit_sources=False, workers=1,
        adapter_id="fixture", canonical_repository="other/overlay",
        canonical_url="https://example.com/other.git")

    assert report["ok"] is False
    assert report["errors"][0]["stage"] == "local-repository"
    assert "no Git remote matches" in report["errors"][0]["message"]


def test_time_window_is_complete_or_limit_truncated(tmp_path):
    repository = make_repository(tmp_path / "overlay", commits=5)
    since = datetime(2025, 1, 3, tzinfo=timezone.utc)
    root_revision = git(repository, "rev-list", "--max-parents=0", "main")

    bootstrap = collector.collect_local(
        repository, "main", limit=5, audit_sources=False, workers=1,
        since=since, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)
    truncated = collector.collect_local(
        repository, "main", limit=2, audit_sources=False, workers=1,
        since=since, after_revision=root_revision, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    assert bootstrap["history"]["commits_in_window"] == 3
    assert bootstrap["history"]["time_boundary_reached"] is True
    assert bootstrap["history"]["time_window_complete"] is True
    assert bootstrap["history_complete"] is False
    assert bootstrap["history"]["truncation_reason"] == "missing-cursor"
    assert "bootstrap discovery" in bootstrap["limitations"][0]
    assert truncated["ok"] is False
    assert truncated["truncated"] is True
    assert truncated["history"]["limit_truncated"] is True
    assert truncated["history"]["truncation_reason"] == "limit"


def test_non_ancestor_cursor_is_structured_error(tmp_path):
    repository = make_repository(tmp_path / "overlay", commits=2)
    tree = git(repository, "rev-parse", "main^{tree}")
    unrelated = git(repository, "commit-tree", tree, "-m", "unrelated tip")

    report = collector.collect_local(
        repository, "main", limit=10, audit_sources=False, workers=1,
        after_revision=unrelated, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    assert report["history"]["state"] == "error"
    assert report["errors"][0]["stage"] == "local-repository"
    assert "not an ancestor" in report["errors"][0]["message"]


def mocked_remote(monkeypatch, histories):
    revision = "a" * 40

    def fake_git(repository, *arguments, check=True):
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(collector, "run_git", fake_git)
    monkeypatch.setattr(collector, "resolve_ref", lambda repository, ref: revision)
    monkeypatch.setattr(
        collector, "repository_identity",
        lambda repository, ref: {
            "repo_name": "remote-overlay",
            "layout": {"masters": "gentoo"},
        })
    monkeypatch.setattr(
        collector, "inspect_history",
        lambda repository, ref, limit, attempts, since, after_revision:
        histories[attempts - 1])
    monkeypatch.setattr(collector, "collect_candidates", lambda *args: [])
    monkeypatch.setattr(collector, "official_source_records", lambda *args: [])


def test_remote_history_deepens_at_most_three_times(monkeypatch):
    histories = [
        collector.history_record(
            "truncated", 8, count, True, attempt,
            after_revision="b" * 40, cursor_state="not-retrieved",
            truncation_reason="cursor-not-retrieved")
        for attempt, count in enumerate((1, 2, 4), 1)
    ]
    mocked_remote(monkeypatch, histories)

    report = collector.collect_remote(
        "https://example.com/overlay.git", "main", limit=8, initial_depth=1,
        audit_sources=False, workers=1, after_revision="b" * 40,
        adapter_id="fixture", canonical_repository="example/overlay")

    assert report["ok"] is False
    assert report["history"]["state"] == "truncated"
    assert report["history"]["retrieval_attempts"] == 3
    assert report["history"]["deepening_attempts"] == 2


def test_remote_cursor_completes_after_deepening(monkeypatch):
    cursor = "b" * 40
    histories = [
        collector.history_record(
            "truncated", 8, 2, True, 1, after_revision=cursor,
            cursor_state="not-retrieved",
            truncation_reason="cursor-not-retrieved"),
        collector.history_record(
            "complete", 8, 3, True, 2, after_revision=cursor,
            cursor_state="verified"),
    ]
    mocked_remote(monkeypatch, histories)

    report = collector.collect_remote(
        "https://example.com/overlay.git", "main", limit=8, initial_depth=1,
        audit_sources=False, workers=1, after_revision=cursor,
        adapter_id="fixture", canonical_repository="example/overlay")

    assert report["history_complete"] is True
    assert report["history"]["retrieval_attempts"] == 2
    assert report["primary_validation_complete"] is False
    assert report["ok"] is False


@pytest.mark.parametrize("url", [
    "file:///tmp/overlay",
    "git@example.com:overlay.git",
    "https://user@example.com/overlay.git",
    "https://localhost/overlay.git",
    "https://127.0.0.1/overlay.git",
    "https://10.0.0.1/overlay.git",
    "https://[::1]/overlay.git",
])
def test_remote_rejects_unsafe_urls(url):
    report = collector.collect_remote(
        url, "main", limit=8, initial_depth=1,
        audit_sources=False, workers=1, adapter_id="fixture",
        canonical_repository="example/overlay")

    assert report["ok"] is False
    assert report["errors"][0]["stage"] == "remote-validation"
    assert report["source_records"][0]["state"] == "error"


def test_source_audit_error_is_incomplete(monkeypatch, tmp_path):
    repository = make_repository(tmp_path / "overlay", commits=2)
    root_revision = git(repository, "rev-list", "--max-parents=0", "main")

    def fail_sources(sources, lock, workers):
        return [{
            "id": source["id"],
            "title": source["title"],
            "authority": source["authority"],
            "url": source["url"],
            "state": "error",
            "locked": None,
            "observed": {"ok": False, "error": "offline"},
        } for source in sources]

    monkeypatch.setattr(collector.source_manager, "audit", fail_sources)
    report = collector.collect_local(
        repository, "main", limit=2, audit_sources=True, workers=1,
        after_revision=root_revision, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    assert report["ok"] is False
    assert report["complete"] is False
    assert any(source["state"] == "error"
               for source in report["source_records"])
    assert report["errors"][0]["type"] == "SourceRetrievalError"


def test_redirected_primary_source_with_identical_bytes_is_not_validated(
        monkeypatch):
    source = {
        "id": "devmanual-patches",
        "title": "Patch policy",
        "authority": "gentoo-standard",
        "scope": "portable-core",
        "kind": "http",
        "url": "https://trusted.example.invalid/patches",
        "topics": ["patch"],
        "use": "Validate patch guidance.",
    }
    monkeypatch.setattr(collector.source_manager, "load_registry", lambda: {
        "schema": 1,
        "sources": [source],
    })
    monkeypatch.setattr(collector.source_manager, "load_lock", lambda: {
        "schema": 1,
        "sources": {"devmanual-patches": {
            "kind": "http",
            "sha256": "a" * 64,
            "bytes": 12,
            "final_url": "https://trusted.example.invalid/patches",
        }},
    })
    monkeypatch.setattr(collector.source_manager, "observe", lambda _source: {
        "ok": True,
        "kind": "http",
        "sha256": "a" * 64,
        "bytes": 12,
        "final_url": "https://redirected.example.invalid/patches",
    })

    record = collector.official_source_records(
        ["patch"], audit_sources=True, workers=1)[0]

    assert record["state"] == "drift"
    assert record["validated"] is False
    assert record["observed"]["sha256"] == record["locked"]["sha256"]


def test_current_primary_sources_complete_ingestion(monkeypatch, tmp_path):
    repository = make_repository(tmp_path / "overlay", commits=2)
    root_revision = git(repository, "rev-list", "--max-parents=0", "main")

    def current_sources(sources, lock, workers):
        return [{
            "id": source["id"],
            "title": source["title"],
            "authority": source["authority"],
            "url": source["url"],
            "state": "current",
            "locked": {"sha256": "a" * 64},
            "observed": {"ok": True, "sha256": "a" * 64},
        } for source in sources]

    monkeypatch.setattr(collector.source_manager, "audit", current_sources)
    report = collector.collect_local(
        repository, "main", limit=2, audit_sources=True, workers=1,
        after_revision=root_revision, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    assert report["history_complete"] is True
    assert report["primary_validation_complete"] is True
    assert report["complete"] is True
    assert report["ok"] is True


def test_multi_commit_report_ingests_into_evidence_store(monkeypatch, tmp_path):
    repository = make_repository(tmp_path / "overlay", commits=3)
    root_revision = git(repository, "rev-list", "--max-parents=0", "main")

    def current_sources(sources, lock, workers):
        return [{
            "id": source["id"],
            "title": source["title"],
            "authority": source["authority"],
            "url": source["url"],
            "state": "current",
            "locked": {"sha256": "a" * 64},
            "observed": {"ok": True, "sha256": "a" * 64},
        } for source in sources]

    monkeypatch.setattr(collector.source_manager, "audit", current_sources)
    report = collector.collect_local(
        repository, "main", limit=3, audit_sources=True, workers=1,
        after_revision=root_revision, adapter_id="fixture",
        canonical_repository="example/overlay", canonical_url=CANONICAL_URL)

    candidate_sources = [source for source in report["source_records"]
                         if source.get("role") == "candidate"]
    assert len(report["candidates"]) == 2
    assert len(candidate_sources) == 2
    assert {source["revision"] for source in candidate_sources} == {
        candidate["source_revision"] for candidate in report["candidates"]}
    cursor = next(source for source in report["source_records"]
                  if source["id"] == "scope-cursor")
    assert cursor["revision"] == git(repository, "rev-parse", "main")
    assert cursor["repo_name"] == "fixture-overlay"

    with evidence.EvidenceStore(tmp_path / "evidence.db") as store:
        result = store.ingest(report, "qa-style")
        observations = store.list_observations()

    assert result["status"] == "passed"
    assert result["candidates_ingested"] == 2
    assert sum(item["source_id"].startswith("candidate-history:")
               for item in observations) == 2
    assert any(item["source_id"] == "scope-cursor"
               and item["revision"] == cursor["revision"]
               for item in observations)


def test_output_cap_marks_candidate_records_incomplete():
    candidates = [{"value": "x" * 800, "number": number}
                  for number in range(10)]
    report = {
        "complete": True,
        "truncated": False,
        "output_complete": True,
        "ok": True,
        "limitations": [],
        "candidates": candidates,
        "output": {
            "max_bytes": 2000,
            "candidate_records_total": len(candidates),
            "candidate_records_emitted": len(candidates),
        },
    }

    result = collector.enforce_output_cap(report, maximum=2000)

    assert result["output_complete"] is False
    assert result["truncated"] is True
    assert result["ok"] is False
    assert result["output"]["candidate_records_emitted"] < len(candidates)
    assert collector.report_size(result) <= 2000


def test_git_output_limit_stops_streaming(tmp_path):
    repository = make_repository(tmp_path / "overlay")
    large = repository / "large.txt"
    large.write_text("x" * 4096, encoding="utf-8")
    git(repository, "add", "large.txt")
    git(repository, "commit", "--quiet", "-m", "qa: add large output")

    with pytest.raises(collector.GitError, match="output exceeds 64 bytes"):
        collector.run_git(
            repository, "show", "HEAD:large.txt", max_output_bytes=64)


def test_limited_process_timeout_terminates_promptly(tmp_path):
    started = time.monotonic()

    with pytest.raises(collector.GitError, match="timed out after 0.05 seconds"):
        collector.run_limited_process(
            [sys.executable, "-c", "import time; time.sleep(5)"], tmp_path,
            timeout=0.05, max_output_bytes=1024)

    assert time.monotonic() - started < 1


def test_cli_alias_and_atomic_output(tmp_path, capsys):
    repository = make_repository(tmp_path / "overlay")
    output = tmp_path / "report.json"
    tip = git(repository, "rev-parse", "main")

    status = collector.main([
        "--overlay-path", str(repository), "--ref", "main",
        "--since-days", "30", "--after-revision", tip,
        "--adapter-id", "fixture",
        "--canonical-repository", "example/overlay",
        "--canonical-url", CANONICAL_URL, "--audit-sources",
        "--limit", "10", "--output", str(output),
    ])

    assert status == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == 1
    assert report["scope"]["policy_claims"] is False
    assert report["scope"]["since"] is not None
    captured = capsys.readouterr()
    assert captured.out == f"Wrote complete QA/style report to {output}.\n"
    assert captured.err == ""
    assert list(tmp_path.glob(".report.json.*")) == []


def test_cli_output_failure_does_not_fall_back_to_json(
        monkeypatch, tmp_path, capsys):
    repository = make_repository(tmp_path / "overlay")
    output = tmp_path / "report.json"
    tip = git(repository, "rev-parse", "main")

    def fail_write(path, content):
        raise OSError("write failed")

    monkeypatch.setattr(collector, "atomic_write", fail_write)
    status = collector.main([
        "--overlay-path", str(repository), "--ref", "main",
        "--after-revision", tip, "--adapter-id", "fixture",
        "--canonical-repository", "example/overlay",
        "--canonical-url", CANONICAL_URL, "--audit-sources",
        "--limit", "10", "--output", str(output),
    ])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert captured.err == (
        f"Could not write QA/style report to {output}: write failed\n")
    assert '"schema_version"' not in captured.err


def test_large_cli_artifact_is_written_without_stdout_duplication(
        monkeypatch, tmp_path, capsys):
    repository = make_repository(tmp_path / "overlay")
    root_revision = git(repository, "rev-parse", "main")
    bulk = repository / "bulk"
    bulk.mkdir()
    suffix = "x" * 180
    for number in range(1500):
        (bulk / f"qa-{number:04d}-{suffix}.txt").write_text(
            "check\n", encoding="utf-8")
    git(repository, "add", "bulk")
    git(repository, "commit", "--quiet", "-m", "qa: add large fixture")

    def current_sources(sources, lock, workers):
        return [{
            "id": source["id"],
            "title": source["title"],
            "authority": source["authority"],
            "url": source["url"],
            "state": "current",
            "locked": {"sha256": "a" * 64},
            "observed": {"ok": True, "sha256": "a" * 64},
        } for source in sources]

    monkeypatch.setattr(collector.source_manager, "audit", current_sources)
    output = tmp_path / "large-report.json"
    status = collector.main([
        "--overlay-path", str(repository), "--ref", "main",
        "--after-revision", root_revision, "--adapter-id", "fixture",
        "--canonical-repository", "example/overlay",
        "--canonical-url", CANONICAL_URL, "--audit-sources",
        "--limit", "10", "--output", str(output),
    ])

    captured = capsys.readouterr()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert status == 0
    assert report["complete"] is True
    assert output.stat().st_size > 256 * 1024
    assert captured.out == f"Wrote complete QA/style report to {output}.\n"
    assert len(captured.out.encode("utf-8")) < 512
    assert '"source_records"' not in captured.out
    assert captured.err == ""
    with evidence.EvidenceStore(tmp_path / "large-evidence.db") as store:
        result = store.ingest(report, "qa-style")
    assert result["status"] == "passed"


def test_cli_bounds_limit_and_workers():
    with pytest.raises(SystemExit):
        collector.parse_args([
            "--overlay-path", "/tmp/overlay", "--adapter-id", "fixture",
            "--canonical-repository", "example/overlay", "--limit", "1001"])
    with pytest.raises(SystemExit):
        collector.parse_args([
            "--overlay-path", "/tmp/overlay", "--adapter-id", "fixture",
            "--canonical-repository", "example/overlay", "--workers", "33"])
