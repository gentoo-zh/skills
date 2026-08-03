from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SOURCE_MANAGER_PATH = (
    ROOT / ".agents" / "skills" / "gzh-version-bump" / "scripts"
    / "source_manager.py")
LESSON_LOOKUP_PATH = (
    ROOT / ".agents" / "skills" / "gzh-version-bump" / "scripts"
    / "lesson_lookup.py")


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


source_manager = load_module("source_manager_test", SOURCE_MANAGER_PATH)
lesson_lookup = load_module("lesson_lookup_test", LESSON_LOOKUP_PATH)


def test_source_registry_is_queryable_by_topic():
    registry = source_manager.load_registry()
    selected = source_manager.select_sources(
        registry, ids=[], topic="dependency", authority=None)
    assert selected
    assert all("dependency" in source["topics"] for source in selected)
    assert any(source["authority"] == "gentoo-standard" for source in selected)


def test_source_registry_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "sources.json"
    source = {
        "id": "duplicate",
        "title": "Source",
        "authority": "official",
        "kind": "http",
        "url": "https://example.invalid/",
        "topics": ["test"],
        "use": "Test fixture",
    }
    path.write_text(json.dumps({
        "schema": 1,
        "authorities": ["official"],
        "sources": [source, source],
    }))
    with pytest.raises(ValueError, match="duplicate source id"):
        source_manager.load_registry(path)


def test_source_audit_reports_current_drift_and_error(monkeypatch):
    sources = [
        {"id": "current", "title": "Current", "authority": "official",
         "kind": "git", "url": "https://example.invalid/current.git"},
        {"id": "drift", "title": "Drift", "authority": "official",
         "kind": "http", "url": "https://example.invalid/drift"},
        {"id": "error", "title": "Error", "authority": "official",
         "kind": "http", "url": "https://example.invalid/error"},
    ]
    observations = {
        "current.git": {"ok": True, "kind": "git", "revision": "a" * 40},
        "drift": {"ok": True, "kind": "http", "sha256": "b" * 64},
        "error": {"ok": False, "kind": "http", "error": "offline"},
    }
    monkeypatch.setattr(
        source_manager, "observe",
        lambda source: observations[source["url"].rsplit("/", 1)[-1]])
    lock = {"schema": 1, "sources": {
        "current": {"revision": "a" * 40},
        "drift": {"sha256": "c" * 64},
        "error": {"sha256": "d" * 64},
    }}
    results = source_manager.audit(sources, lock, workers=1)
    assert [result["state"] for result in results] == [
        "current", "drift", "error"]


def test_mediawiki_source_uses_revision_id(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "query": {"pages": [{"revisions": [{"revid": 12345}]}]}
            }).encode()

    monkeypatch.setattr(
        source_manager.urllib.request, "urlopen",
        lambda request, timeout: Response())
    result = source_manager.observe({
        "kind": "mediawiki",
        "api_url": "https://wiki.example.invalid/api.php",
    })
    assert result == {
        "ok": True, "kind": "mediawiki", "revision": "12345"}


def lesson_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "lessons"
    (repo / "data").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "data" / "PROVENANCE.json").write_text(json.dumps({
        "corpus": "gentoo/gentoo",
        "corpus_head": "f" * 40,
        "classified": 2,
    }))
    records = [
        {"sha": "a" * 40, "summary": "slot operator update"},
        {"sha": "short", "summary": "unrelated"},
    ]
    (repo / "data" / "lessons.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records))
    (repo / "docs" / "deps.md").write_text("# Dependencies\n")
    (repo / "docs" / "MINING.md").write_text("# Internal\n")
    return repo


def test_lesson_stats_and_search_preserve_provenance(tmp_path):
    repo = lesson_repo(tmp_path)
    statistics = lesson_lookup.stats(repo)
    assert statistics["classified"] == 2
    assert statistics["lessons"] == 2
    assert statistics["topics"] == ["deps"]
    result = lesson_lookup.search(repo, "slot operator", limit=5)
    assert result[0]["commit_url"] == (
        "https://github.com/gentoo/gentoo/commit/" + "a" * 40)


def test_lesson_refresh_refuses_dirty_checkout(monkeypatch, tmp_path):
    repo = tmp_path / "lessons"
    (repo / ".git").mkdir(parents=True)

    def fake_git(path, *arguments):
        assert path == repo
        if arguments == ("status", "--porcelain"):
            return subprocess.CompletedProcess(arguments, 0, " M data/lessons.jsonl\n", "")
        raise AssertionError(f"unexpected git call: {arguments}")

    monkeypatch.setattr(lesson_lookup, "run_git", fake_git)
    with pytest.raises(RuntimeError, match="dirty"):
        lesson_lookup.refresh(repo)
