from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "gzh"))

from gzh.capabilities import (  # noqa: E402
    CapabilityState,
    OperationBlockedError,
    ProfileValidationError,
    inspect_repository,
    load_bundled_adapter,
    repository_identity,
    require_operation_ready,
    validate_profile,
)


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=repository, check=True,
        capture_output=True, text=True).stdout.strip()


def make_repository(
    path: Path, *, repo_name: str = "gentoo-zh",
    remotes: tuple[tuple[str, str], ...] = (
        ("origin", "git@github.com:gentoo-zh/overlay.git"),),
) -> Path:
    path.mkdir()
    git(path, "init", "--quiet", "--initial-branch=master")
    git(path, "config", "user.name", "Test User")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "profiles").mkdir()
    (path / "profiles" / "repo_name").write_text(
        f"{repo_name}\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "--quiet", "-m", "repository: initialize")
    for name, url in remotes:
        git(path, "remote", "add", name, url)
        git(path, "update-ref", f"refs/remotes/{name}/master", "HEAD")
    return path


def test_bundled_profile_has_provenance_and_all_capability_states():
    adapter = load_bundled_adapter("gentoo-zh")

    assert adapter.adapter_id == "gentoo-zh"
    assert adapter.resolution["default_branch"] == "default-branch"
    assert all(
        set(record["sources"]).issubset(adapter.sources)
        for record in (*adapter.capabilities.values(), *adapter.operations.values()))
    assert {record["state"] for record in adapter.operations.values()} == {
        state.value for state in CapabilityState}
    with pytest.raises(TypeError):
        adapter.capabilities["default-branch"]["value"] = "main"


def test_profile_validation_rejects_missing_provenance_and_unsafe_write_schema():
    profile = load_bundled_adapter("gentoo-zh").as_profile_dict()
    missing_source = copy.deepcopy(profile)
    missing_source["capabilities"]["default-branch"]["sources"] = ["invented"]
    with pytest.raises(ProfileValidationError, match="unknown source identifiers"):
        validate_profile(missing_source)

    unsafe_write = copy.deepcopy(profile)
    unsafe_write["operations"]["repository-write-preflight"]["value"][
        "requires_capabilities"] = ["local-gates"]
    with pytest.raises(ProfileValidationError, match="write operations must require"):
        validate_profile(unsafe_write)


@pytest.mark.parametrize("url, expected", [
    ("git@github.com:gentoo-zh/overlay.git", ("github.com", "gentoo-zh/overlay")),
    ("https://github.com/gentoo-zh/overlay.git", ("github.com", "gentoo-zh/overlay")),
    ("ssh://git@github.com/gentoo-zh/overlay.git", ("github.com", "gentoo-zh/overlay")),
    ("/local/repository", None),
])
def test_repository_identity_normalizes_network_urls(url, expected):
    assert repository_identity(url) == expected


def test_inspection_resolves_clean_synchronized_direct_checkout(tmp_path):
    repository = make_repository(tmp_path / "overlay")
    adapter = load_bundled_adapter("gentoo-zh")

    report = inspect_repository(
        repository, adapter, operation="repository-write-preflight")

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["repository"]["root"]["value"] == str(repository.resolve())
    assert report["repository"]["repo_name"]["value"] == "gentoo-zh"
    assert report["repository"]["canonical_remote"]["value"]["name"] == "origin"
    assert report["repository"]["default_branch"]["value"] == "master"
    assert report["repository"]["clean"]["value"] is True
    assert report["repository"]["ahead"]["value"] == 0
    assert report["repository"]["behind"]["value"] == 0
    assert report["operation"]["write_ready"] is True
    require_operation_ready(report)


def test_canonical_remote_uses_url_and_preserves_upstream_preference(tmp_path):
    repository = make_repository(
        tmp_path / "overlay",
        remotes=(
            ("origin", "git@github.com:someone/fork.git"),
            ("upstream", "https://github.com/gentoo-zh/overlay.git"),
        ))

    report = inspect_repository(repository, load_bundled_adapter("gentoo-zh"))

    remote = report["repository"]["canonical_remote"]
    assert remote["state"] == "known"
    assert remote["value"]["name"] == "upstream"
    assert remote["value"]["profile_identity"]["path"] == "gentoo-zh/overlay"


def test_equivalent_canonical_aliases_are_reported_and_allowed(tmp_path):
    repository = make_repository(
        tmp_path / "overlay",
        remotes=(
            ("source-a", "git@github.com:gentoo-zh/overlay.git"),
            ("source-b", "https://github.com/gentoo-zh/overlay.git"),
        ))

    report = inspect_repository(
        repository, load_bundled_adapter("gentoo-zh"),
        operation="repository-write-preflight")

    remote = report["repository"]["canonical_remote"]
    assert remote["state"] == "known"
    assert {item["name"] for item in remote["value"]["aliases"]} == {
        "source-a", "source-b"}
    assert report["operation"]["ready"] is True


def test_divergent_canonical_aliases_are_explicit_and_block_writes(tmp_path):
    repository = make_repository(
        tmp_path / "overlay",
        remotes=(
            ("source-a", "git@github.com:gentoo-zh/overlay.git"),
            ("source-b", "https://github.com/gentoo-zh/overlay.git"),
        ))
    (repository / "change").write_text("second\n", encoding="utf-8")
    git(repository, "add", "change")
    git(repository, "commit", "--quiet", "-m", "repository: second")
    git(repository, "update-ref", "refs/remotes/source-a/master", "HEAD")

    report = inspect_repository(
        repository, load_bundled_adapter("gentoo-zh"),
        operation="repository-write-preflight")

    remote = report["repository"]["canonical_remote"]
    assert remote["state"] == "unknown"
    assert "do not have one verified" in remote["reason"]
    assert report["operation"]["ready"] is False
    with pytest.raises(OperationBlockedError, match="canonical_remote"):
        require_operation_ready(report)


def test_conflicting_repo_name_and_dirty_checkout_block_writes(tmp_path):
    wrong = make_repository(tmp_path / "wrong", repo_name="another-overlay")
    wrong_report = inspect_repository(
        wrong, load_bundled_adapter("gentoo-zh"),
        operation="repository-write-preflight")
    assert wrong_report["repository"]["identity"]["state"] == "unknown"
    assert wrong_report["operation"]["write_ready"] is False

    dirty = make_repository(tmp_path / "dirty")
    (dirty / "untracked").write_text("change\n", encoding="utf-8")
    dirty_report = inspect_repository(
        dirty, load_bundled_adapter("gentoo-zh"),
        operation="repository-write-preflight")
    assert dirty_report["repository"]["clean"] == {
        "state": "known",
        "provenance": [{
            "kind": "command",
            "location": "git status --porcelain=v1 --untracked-files=normal",
        }],
        "value": False,
    }
    assert any(
        blocker["field"] == "runtime.clean"
        and blocker["state"] == "known"
        for blocker in dirty_report["operation"]["blockers"])


@pytest.mark.parametrize("operation, state", [
    ("publication", "unknown"),
    ("unattended-publication", "unsupported"),
    ("invented-write", "unknown"),
])
def test_unknown_and_unsupported_operations_fail_closed(tmp_path, operation, state):
    repository = make_repository(tmp_path / operation)

    report = inspect_repository(
        repository, load_bundled_adapter("gentoo-zh"), operation=operation)

    assert report["operation"]["state"] == state
    assert report["operation"]["write"] is True
    assert report["operation"]["write_ready"] is False
    with pytest.raises(OperationBlockedError):
        require_operation_ready(report)
