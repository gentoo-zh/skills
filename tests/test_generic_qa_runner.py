from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = (ROOT / ".agents" / "skills" / "gentoo-overlay-development"
               / "scripts" / "qa_runner.py")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "generic_qa_runner_test", RUNNER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


qa = load_module()


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=True,
        capture_output=True, text=True).stdout.strip()


def make_repository(path: Path, *, origin: str) -> Path:
    path.mkdir()
    git(path, "init", "--quiet", "--initial-branch=main")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "remote", "add", "origin", origin)
    (path / "profiles").mkdir()
    (path / "metadata").mkdir()
    (path / "profiles" / "repo_name").write_text(
        "shared-name\n", encoding="utf-8")
    (path / "metadata" / "layout.conf").write_text(
        "masters = gentoo\nthin-manifests = true\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "repository: initialize")
    return path


def fake_pkgcheck(stdout="", returncode=0, stderr=""):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="pkgcheck 0.test\n", stderr="")
        return subprocess.CompletedProcess(
            command, returncode, stdout=stdout, stderr=stderr)

    return run, calls


def collect(repository: Path, fake, **kwargs):
    return qa.collect(
        repository, "fixture", kwargs.pop("canonical_repository", "org/repo"),
        runner=fake, which=lambda _name: "/usr/bin/pkgcheck", **kwargs)


def test_clean_scan_records_command_identity_and_official_evidence(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/fork.git")
    fake, calls = fake_pkgcheck()

    report = collect(repository, fake, severity="error", target="cat/pkg")

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["truncated"] is False
    assert report["findings"] == []
    assert report["scope"] == {
        "adapter_id": "fixture",
        "adapter_identity_state": "configured-unverified",
        "canonical_repository": "org/repo",
        "canonical_repository_identity_state": "configured-unverified",
        "severity_threshold": "error",
        "target": "cat/pkg",
        "network_enabled": False,
    }
    assert report["repository"]["repo_name"] == "shared-name"
    assert report["repository"]["git_branch"] == "main"
    assert report["repository"]["git_dirty"] is False
    assert report["repository"]["configured_origin"] == (
        "https://example.com/fork.git")
    command = calls[1][0]
    assert command[:4] == [
        "/usr/bin/pkgcheck", "scan", "-R", "JsonStream"]
    assert command[command.index("--exit") + 1] == "error"
    assert command[command.index("--repo") + 1] == str(repository)
    assert "--net" not in command
    assert command[-1] == "cat/pkg"
    evidence = report["official_evidence"]
    assert evidence["source"]["id"] == "pkgcheck"
    assert evidence["source"]["scope"] == "portable-core"
    assert evidence["reviewed_lock"]["sha256"]
    assert evidence["source_network_audit_performed"] is False


def test_finding_is_complete_but_fails_severity_gate(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    output = json.dumps({
        "__class__": "NonexistentDeps",
        "category": "cat",
        "package": "pkg",
        "version": "1",
    }) + "\n"
    fake, _calls = fake_pkgcheck(stdout=output, returncode=1)

    report = collect(repository, fake)

    assert report["ok"] is False
    assert report["complete"] is True
    assert report["truncated"] is False
    assert report["findings"][0]["code"] == "NonexistentDeps"
    assert report["execution"]["returncode"] == 1
    assert report["errors"] == []


def test_pkgcheck_error_is_structured_and_incomplete(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    fake, _calls = fake_pkgcheck(returncode=2, stderr="internal failure")

    report = collect(repository, fake)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["errors"][0]["stage"] == "pkgcheck"
    assert "returncode 2" in report["errors"][0]["message"]


def test_missing_pkgcheck_is_structured(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")

    report = qa.collect(
        repository, "fixture", "org/repo", which=lambda _name: None)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["errors"][0]["type"] == "FileNotFoundError"


def test_invalid_json_and_oversized_output_are_rejected(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    invalid, _calls = fake_pkgcheck(stdout="not-json\n", returncode=1)
    malformed = collect(repository, invalid)

    large_line = json.dumps({"__class__": "Large", "value": "x" * 500})
    oversized_fake, _calls = fake_pkgcheck(stdout=large_line)
    oversized = collect(
        repository, oversized_fake, max_output_bytes=256)

    assert malformed["complete"] is False
    assert malformed["truncated"] is False
    assert "invalid JSON" in malformed["errors"][0]["message"]
    assert oversized["complete"] is False
    assert oversized["truncated"] is True
    assert oversized["errors"][0]["type"] == "OverflowError"


def test_timeout_is_truncated_and_structured(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")

    def timeout(command, **kwargs):
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="pkgcheck 0.test\n", stderr="")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    report = collect(repository, timeout, timeout=2)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["errors"][0]["type"] == "TimeoutError"


def test_real_subprocess_is_terminated_at_output_limit(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    executable = tmp_path / "pkgcheck-fixture"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--version' in sys.argv:\n"
        "    print('pkgcheck 0.test')\n"
        "else:\n"
        "    print('x' * 1000000)\n",
        encoding="utf-8")
    executable.chmod(0o755)

    report = qa.collect(
        repository, "fixture", "org/repo", max_output_bytes=256,
        which=lambda _name: str(executable))

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["errors"][0]["type"] == "OverflowError"


def test_network_checks_require_explicit_flag(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    fake, calls = fake_pkgcheck()

    report = collect(repository, fake, net=True)

    assert report["scope"]["network_enabled"] is True
    assert "--net" in calls[1][0]
    assert report["official_evidence"][
        "source_network_audit_performed"] is False
    assert report["network_checks"] == {
        "requested": True, "attempted": True, "completed": True}


@pytest.mark.parametrize("failure", ["error", "timeout"])
def test_failed_network_scan_is_attempted_but_not_completed(
        tmp_path, failure):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")

    def run(command, **kwargs):
        if command[1:] == ["--version"]:
            return subprocess.CompletedProcess(
                command, 0, stdout="pkgcheck 0.test\n", stderr="")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        return subprocess.CompletedProcess(
            command, 2, stdout="", stderr="internal failure")

    report = collect(repository, run, net=True, timeout=2)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["network_checks"] == {
        "requested": True, "attempted": True, "completed": False}


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_dirty_worktree_is_rejected_before_pkgcheck(
        tmp_path, dirty_kind):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    if dirty_kind == "tracked":
        (repository / "metadata" / "layout.conf").write_text(
            "masters = gentoo\nthin-manifests = false\n", encoding="utf-8")
    else:
        (repository / "untracked").write_text("dirty\n", encoding="utf-8")
    fake, calls = fake_pkgcheck()

    report = collect(repository, fake)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["repository"]["git_dirty"] is True
    assert report["errors"][0]["stage"] == "repository"
    assert calls == []


def test_same_repo_name_does_not_define_canonical_identity(tmp_path):
    first = make_repository(
        tmp_path / "first", origin="https://example.com/first-fork.git")
    second = make_repository(
        tmp_path / "second", origin="https://example.com/second-fork.git")
    first_fake, _calls = fake_pkgcheck()
    second_fake, _calls = fake_pkgcheck()

    first_report = collect(
        first, first_fake, canonical_repository="owner/first")
    second_report = collect(
        second, second_fake, canonical_repository="owner/second")

    assert first_report["repository"]["repo_name"] == "shared-name"
    assert second_report["repository"]["repo_name"] == "shared-name"
    assert first_report["scope"]["canonical_repository"] == "owner/first"
    assert second_report["scope"]["canonical_repository"] == "owner/second"
    assert first_report["repository"]["configured_origin"] != (
        second_report["repository"]["configured_origin"])


@pytest.mark.parametrize("target", [
    "--net", "../cat/pkg", "/cat/pkg", "cat/pkg other", "cat/pkg;command",
])
def test_unsafe_target_is_rejected_before_pkgcheck(tmp_path, target):
    repository = make_repository(
        tmp_path / target.replace("/", "_").replace(" ", "_"),
        origin="https://example.com/overlay.git")
    fake, calls = fake_pkgcheck()

    report = collect(repository, fake, target=target)

    assert report["ok"] is False
    assert report["repository"] is None
    assert calls == []


def test_output_inside_repository_is_rejected(tmp_path):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    fake, calls = fake_pkgcheck()

    report = collect(
        repository, fake, output_path=repository / "qa-report.json")

    assert report["ok"] is False
    assert "outside" in report["errors"][0]["message"]
    assert calls == []


def test_cli_never_writes_failed_report_inside_repository(tmp_path, capsys):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    output = repository / "qa-report.json"

    status = qa.main([
        "--repository", str(repository),
        "--adapter-id", "fixture",
        "--canonical-repository", "org/repo",
        "--target=--unsafe",
        "--output", str(output),
    ])

    assert status == 1
    assert not output.exists()
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_cli_writes_json_atomically_outside_repository(
        monkeypatch, tmp_path, capsys):
    repository = make_repository(
        tmp_path / "overlay", origin="https://example.com/overlay.git")
    output = tmp_path / "report.json"
    fake, _calls = fake_pkgcheck()
    original_collect = qa.collect

    def mocked_collect(*args, **kwargs):
        return original_collect(
            *args, **kwargs, runner=fake,
            which=lambda _name: "/usr/bin/pkgcheck")

    monkeypatch.setattr(qa, "collect", mocked_collect)

    status = qa.main([
        "--repository", str(repository),
        "--adapter-id", "fixture",
        "--canonical-repository", "org/repo",
        "--output", str(output),
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    stdout = capsys.readouterr().out
    assert status == 0
    assert stdout == f"Wrote complete QA report to {output.resolve()}.\n"
    assert json.dumps(report, ensure_ascii=False, indent=2) not in stdout
    assert list(tmp_path.glob(".report.json.*")) == []
    assert git(repository, "status", "--porcelain=v1") == ""
