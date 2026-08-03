from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SOURCE_MANAGER_PATH = (
    ROOT / ".agents" / "skills" / "gentoo-overlay-development" / "scripts"
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
        "scope": "portable-core",
        "kind": "http",
        "url": "https://example.invalid/",
        "topics": ["test"],
        "use": "Test fixture",
    }
    path.write_text(json.dumps({
        "schema": 1,
        "authorities": ["official"],
        "authority_scopes": {"official": ["portable-core"]},
        "scopes": ["portable-core"],
        "sources": [source, source],
    }))
    with pytest.raises(ValueError, match="duplicate source id"):
        source_manager.load_registry(path)


def test_source_registry_rejects_authority_outside_allowed_scope(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(json.dumps({
        "schema": 1,
        "authorities": ["official"],
        "authority_scopes": {"official": ["portable-core"]},
        "scopes": ["portable-core", "comparative-evidence"],
        "sources": [{
            "id": "misclassified",
            "title": "Misclassified source",
            "authority": "official",
            "scope": "comparative-evidence",
            "kind": "http",
            "url": "https://example.invalid/",
            "topics": ["test"],
            "use": "Test fixture",
        }],
    }))

    with pytest.raises(ValueError, match="not allowed in source scope"):
        source_manager.load_registry(path)


def test_explicit_source_id_must_match_every_filter():
    registry = source_manager.load_registry()
    with pytest.raises(ValueError, match="do not match the selected filters"):
        source_manager.select_sources(
            registry, ["overlay-policy"], None, None, "portable-core")


@pytest.mark.parametrize("selector", [
    [],
    ["--all-scopes"],
    ["--scope", "portable-core"],
    ["--topic", "dependency"],
    ["--authority", "gentoo-standard"],
])
def test_refresh_lock_requires_explicit_source_ids(selector):
    result = subprocess.run(
        [sys.executable, str(SOURCE_MANAGER_PATH), "refresh-lock", *selector],
        cwd=ROOT, capture_output=True, text=True)

    assert result.returncode == 2
    assert "--id" in result.stderr


def test_source_audit_reports_current_drift_and_error(monkeypatch):
    sources = [
        {"id": "current", "title": "Current", "authority": "official",
         "scope": "portable-core",
         "kind": "git", "url": "https://example.invalid/current.git"},
        {"id": "drift", "title": "Drift", "authority": "official",
         "scope": "portable-core",
         "kind": "http", "url": "https://example.invalid/drift"},
        {"id": "error", "title": "Error", "authority": "official",
         "scope": "portable-core",
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


def test_http_source_redirect_change_is_drift_with_identical_bytes(monkeypatch):
    source = {
        "id": "policy",
        "title": "Policy",
        "authority": "gentoo-standard",
        "scope": "portable-core",
        "kind": "http",
        "url": "https://trusted.example.invalid/policy",
    }
    monkeypatch.setattr(source_manager, "observe", lambda _source: {
        "ok": True,
        "kind": "http",
        "sha256": "a" * 64,
        "bytes": 12,
        "final_url": "https://redirected.example.invalid/policy",
    })
    lock = {"schema": 1, "sources": {"policy": {
        "kind": "http",
        "sha256": "a" * 64,
        "bytes": 12,
        "final_url": "https://trusted.example.invalid/policy",
    }}}

    result = source_manager.audit([source], lock, workers=1)[0]

    assert result["state"] == "drift"
    assert result["observed"]["sha256"] == result["locked"]["sha256"]


def test_mediawiki_source_uses_revision_id(monkeypatch):
    class Response:
        def __init__(self):
            self.payload = json.dumps({
                "query": {"pages": [{"revisions": [{"revid": 12345}]}]}
            }).encode()
            self.offset = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size=-1):
            if size < 0:
                size = len(self.payload) - self.offset
            chunk = self.payload[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    monkeypatch.setattr(
        source_manager.urllib.request, "urlopen",
        lambda request, timeout: Response())
    result = source_manager.observe_network_direct(
        "mediawiki", "https://wiki.example.invalid/api.php",
        source_manager.MAX_MEDIAWIKI_RESPONSE_BYTES)
    assert result == {
        "ok": True, "kind": "mediawiki", "revision": "12345"}


class BoundedResponse:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if size < 0:
            size = len(self.payload) - self.offset
        chunk = self.payload[self.offset:self.offset + size]
        self.offset += len(chunk)
        return chunk

    def geturl(self):
        return "https://example.invalid/final"


def test_http_source_accepts_a_response_at_the_size_limit(monkeypatch):
    payload = b"x" * 32
    monkeypatch.setattr(source_manager, "MAX_HTTP_RESPONSE_BYTES", len(payload))
    monkeypatch.setattr(
        source_manager.urllib.request, "urlopen",
        lambda request, timeout: BoundedResponse(payload))

    result = source_manager.observe_network_direct(
        "http", "https://example.invalid/source",
        source_manager.MAX_HTTP_RESPONSE_BYTES)

    assert result["ok"] is True
    assert result["bytes"] == len(payload)
    assert result["final_url"] == "https://example.invalid/final"


def test_http_source_rejects_an_oversized_response_while_reading(monkeypatch):
    payload = b"x" * 33
    monkeypatch.setattr(source_manager, "MAX_HTTP_RESPONSE_BYTES", 32)
    monkeypatch.setattr(
        source_manager.urllib.request, "urlopen",
        lambda request, timeout: BoundedResponse(payload))

    with pytest.raises(ValueError, match="response exceeds 32 bytes"):
        source_manager.observe_network_direct(
            "http", "https://example.invalid/source",
            source_manager.MAX_HTTP_RESPONSE_BYTES)


def test_mediawiki_source_rejects_an_oversized_response_while_reading(
        monkeypatch):
    payload = b"x" * 33
    monkeypatch.setattr(source_manager, "MAX_MEDIAWIKI_RESPONSE_BYTES", 32)
    monkeypatch.setattr(
        source_manager.urllib.request, "urlopen",
        lambda request, timeout: BoundedResponse(payload))

    with pytest.raises(ValueError, match="response exceeds 32 bytes"):
        source_manager.observe_network_direct(
            "mediawiki", "https://wiki.example.invalid/api.php",
            source_manager.MAX_MEDIAWIKI_RESPONSE_BYTES)


def test_network_source_uses_bounded_process_deadline(monkeypatch):
    captured = {}

    def fake_process(command, *, timeout, maximum):
        captured.update(command=command, timeout=timeout, maximum=maximum)
        result = {
            "ok": True,
            "kind": "http",
            "sha256": "a" * 64,
            "bytes": 1,
            "final_url": "https://example.invalid/final",
        }
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(source_manager, "bounded_process", fake_process)

    result = source_manager.observe({
        "kind": "http", "url": "https://example.invalid/source"})

    assert result["ok"] is True
    assert captured["timeout"] == source_manager.NETWORK_TOTAL_TIMEOUT_SECONDS
    assert captured["maximum"] == source_manager.MAX_NETWORK_RESULT_BYTES
    assert captured["command"][2] == "_observe-network"


def test_bounded_process_terminates_after_total_deadline():
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="command timed out"):
        source_manager.bounded_process(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout=0.1, maximum=1024)

    assert time.monotonic() - started < 2


@pytest.mark.parametrize("workers", [0, source_manager.MAX_AUDIT_WORKERS + 1])
def test_source_audit_rejects_workers_outside_limit(workers):
    with pytest.raises(ValueError, match="workers must be between"):
        source_manager.audit([], {"schema": 1, "sources": {}}, workers)


@pytest.mark.parametrize("descriptor", [1, 2])
def test_git_source_rejects_oversized_process_output(
        tmp_path, monkeypatch, descriptor):
    fake_git = tmp_path / "git"
    fake_git.write_text(
        f"#!{sys.executable}\n"
        f"import os\nos.write({descriptor}, b'x' * 4096)\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setattr(source_manager, "MAX_GIT_OUTPUT_BYTES", 64)

    result = source_manager.observe({
        "kind": "git",
        "url": "https://example.invalid/repository.git",
    })

    assert result == {
        "ok": False,
        "kind": "git",
        "error": "RuntimeError: command output exceeded 64 bytes",
    }


def test_git_source_preserves_the_revision_result(monkeypatch, tmp_path):
    revision = "a" * 40
    fake_git = tmp_path / "git"
    fake_git.write_text(
        f"#!{sys.executable}\n"
        f"import os\nos.write(1, b'{revision}\\tHEAD\\n')\n")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    result = source_manager.observe({
        "kind": "git",
        "url": "https://example.invalid/repository.git",
    })

    assert result == {"ok": True, "kind": "git", "revision": revision}


def test_git_ref_rejects_multiple_or_mismatched_results(monkeypatch):
    monkeypatch.setattr(
        source_manager, "bounded_process",
        lambda command, timeout, maximum: subprocess.CompletedProcess(
            command, 0,
            stdout=("a" * 40 + "\trefs/heads/main\n"
                    + "b" * 40 + "\trefs/heads/main\n"),
            stderr=""))

    with pytest.raises(RuntimeError, match="exactly one result"):
        source_manager.resolve_git_ref(
            "https://example.invalid/repository.git", "refs/heads/main")


def test_git_ref_rejects_non_commit_revision(monkeypatch):
    monkeypatch.setattr(
        source_manager, "bounded_process",
        lambda command, timeout, maximum: subprocess.CompletedProcess(
            command, 0, stdout="short\trefs/heads/main\n", stderr=""))

    with pytest.raises(RuntimeError, match="invalid revision"):
        source_manager.resolve_git_ref(
            "https://example.invalid/repository.git", "refs/heads/main")


def lock_result(source_id: str, revision: str) -> dict:
    return {
        "id": source_id,
        "observed": {
            "ok": True,
            "kind": "git",
            "revision": revision,
        },
    }


def test_lock_update_preserves_original_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "source-lock.json"
    original = json.dumps({
        "schema": 1,
        "sources": {"existing": {"revision": "a" * 40}},
    }) + "\n"
    path.write_text(original)

    def fail_replace(source, destination):
        raise OSError("injected replacement failure")

    monkeypatch.setattr(source_manager.os, "replace", fail_replace)

    with pytest.raises(OSError, match="injected replacement failure"):
        source_manager.update_lock(
            [lock_result("reviewed", "b" * 40)], path)

    assert path.read_text() == original
    assert list(tmp_path.glob(".source-lock.json.*")) == []


def test_concurrent_lock_updates_are_serialized_and_merged(tmp_path):
    path = tmp_path / "source-lock.json"
    path.write_text(json.dumps({
        "schema": 1,
        "sources": {"existing": {"revision": "a" * 40}},
    }) + "\n")
    path.chmod(0o640)
    worker = """
import importlib.util
import json
import pathlib
import sys

spec = importlib.util.spec_from_file_location("source_manager_worker", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("ready", flush=True)
module.update_lock([json.loads(sys.argv[3])], pathlib.Path(sys.argv[2]))
"""
    commands = [
        [sys.executable, "-c", worker, str(SOURCE_MANAGER_PATH), str(path),
         json.dumps(lock_result("writer-a", "b" * 40))],
        [sys.executable, "-c", worker, str(SOURCE_MANAGER_PATH), str(path),
         json.dumps(lock_result("writer-b", "c" * 40))],
    ]

    processes = []
    try:
        with source_manager.exclusive_lock(path):
            processes = [subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True) for command in commands]
            assert all(process.stdout.readline().strip() == "ready"
                       for process in processes)
            time.sleep(0.1)
            assert all(process.poll() is None for process in processes)
        outputs = [process.communicate(timeout=5) for process in processes]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert all(process.returncode == 0 for process in processes), outputs
    lock = source_manager.load_lock(path)
    assert set(lock["sources"]) == {"existing", "writer-a", "writer-b"}
    assert lock["sources"]["existing"] == {"revision": "a" * 40}
    assert path.stat().st_mode & 0o777 == 0o640


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
