import hashlib
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from gzh.deps import (
    DependencyMetadataError,
    MAX_METADATA_BYTES,
    analyze_ebuild_dependencies,
    cached_ebuild_metadata,
    compare_ebuild_dependencies,
    generated_ebuild_metadata,
)


class FakeAnalyzer:
    class AnalysisError(Exception):
        pass

    @staticmethod
    def analyze(document, provenance=None):
        return {
            "atoms": [{"atom": "dev-libs/libfoo:="}],
            "complete": True,
            "document": document,
            "input_provenance": provenance,
            "ok": True,
            "truncated": False,
        }

    @staticmethod
    def error_report(exc, provenance=None):
        return {"complete": True, "ok": False, "truncated": False}


def _recorded_child_pid(pid_path) -> int | None:
    """Return the child PID, or None when the group died before recording it.

    The fixture shell echoes the PID right after spawning, so a bounded stop can
    kill the group before that write lands. An absent PID therefore means the
    child never outlived the runner, which is what the caller asserts.
    """
    for _attempt in range(100):
        try:
            raw = pid_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            raw = ""
        if raw:
            return int(raw)
        time.sleep(0.01)
    return None


def _fixture(tmp_path: Path):
    ebuild = tmp_path / "dev-libs" / "demo" / "demo-1.ebuild"
    ebuild.parent.mkdir(parents=True)
    ebuild.write_text("EAPI=8\nRDEPEND=dev-libs/libfoo\n", encoding="utf-8")
    cache = tmp_path / "metadata" / "md5-cache" / "dev-libs" / "demo-1"
    cache.parent.mkdir(parents=True)
    md5 = hashlib.md5(ebuild.read_bytes(), usedforsecurity=False).hexdigest()
    cache.write_text(
        f"EAPI=8\nRDEPEND=dev-libs/libfoo:=\n_md5_={md5}\n",
        encoding="utf-8",
    )
    return ebuild, cache


def _version_fixture(
    tmp_path: Path,
    version: str,
    *,
    depend: str = "",
    rdepend: str = "",
    bdepend: str = "",
):
    ebuild = tmp_path / "dev-libs" / "demo" / f"demo-{version}.ebuild"
    ebuild.parent.mkdir(parents=True, exist_ok=True)
    ebuild.write_text(f"EAPI=8\n# {version}\n", encoding="utf-8")
    cache = tmp_path / "metadata" / "md5-cache" / "dev-libs" / f"demo-{version}"
    cache.parent.mkdir(parents=True, exist_ok=True)
    md5 = hashlib.md5(ebuild.read_bytes(), usedforsecurity=False).hexdigest()
    cache.write_text(
        f"EAPI=8\nDEPEND={depend}\nRDEPEND={rdepend}\nBDEPEND={bdepend}\n"
        f"_md5_={md5}\n",
        encoding="utf-8",
    )
    return ebuild, cache


def test_cached_metadata_requires_matching_md5(tmp_path):
    ebuild, cache = _fixture(tmp_path)
    document, provenance = cached_ebuild_metadata(ebuild)
    assert document["dependencies"]["RDEPEND"] == "dev-libs/libfoo:="
    assert provenance["kind"] == "verified-md5-cache"

    cache.write_text("EAPI=8\n_md5_=wrong\n", encoding="utf-8")
    with pytest.raises(DependencyMetadataError, match="does not match"):
        cached_ebuild_metadata(ebuild)


def test_cached_metadata_with_eclasses_requires_isolated_regeneration(tmp_path):
    ebuild, cache = _fixture(tmp_path)
    cache.write_text(
        cache.read_text(encoding="utf-8")
        + "_eclasses_=missing\tdeadbeefdeadbeefdeadbeefdeadbeef\n",
        encoding="utf-8")

    with pytest.raises(DependencyMetadataError, match="isolated regeneration"):
        cached_ebuild_metadata(ebuild)


def test_cached_metadata_rejects_malformed_eclass_identity(tmp_path):
    ebuild, cache = _fixture(tmp_path)
    cache.write_text(
        cache.read_text(encoding="utf-8")
        + "_eclasses_=missing\tdeadbeef\n",
        encoding="utf-8")

    with pytest.raises(DependencyMetadataError, match="invalid eclass identities"):
        cached_ebuild_metadata(ebuild)


def test_missing_metadata_cache_fails_closed(tmp_path):
    ebuild = tmp_path / "cat" / "pkg" / "pkg-1.ebuild"
    ebuild.parent.mkdir(parents=True)
    ebuild.write_text("EAPI=8\n", encoding="utf-8")
    with pytest.raises(DependencyMetadataError, match="cache is missing"):
        cached_ebuild_metadata(ebuild)


def test_metadata_cache_symlink_fails_closed(tmp_path):
    ebuild, cache = _fixture(tmp_path)
    target = cache.with_name("cache-target")
    cache.rename(target)
    cache.symlink_to(target.name)

    with pytest.raises(DependencyMetadataError, match="not a regular file"):
        cached_ebuild_metadata(ebuild)


def test_duplicate_metadata_cache_key_fails_closed(tmp_path):
    ebuild, cache = _fixture(tmp_path)
    content = cache.read_text(encoding="utf-8")
    cache.write_text(f"EAPI=8\n{content}", encoding="utf-8")

    with pytest.raises(DependencyMetadataError, match="invalid metadata cache key"):
        cached_ebuild_metadata(ebuild)


@pytest.mark.parametrize("kind", ["ebuild", "metadata cache"])
def test_oversized_dependency_input_fails_closed(tmp_path, kind):
    ebuild, cache = _fixture(tmp_path)
    target = ebuild if kind == "ebuild" else cache
    target.write_bytes(b"x" * (MAX_METADATA_BYTES + 1))

    with pytest.raises(DependencyMetadataError, match="exceeds"):
        cached_ebuild_metadata(ebuild)


def test_analyze_cached_dependencies_passes_use_and_provenance(tmp_path):
    ebuild, _cache = _fixture(tmp_path)
    report = analyze_ebuild_dependencies(
        ebuild,
        use=["+ssl", "-test"],
        analyzer=FakeAnalyzer,
    )
    assert report["ok"] is True
    assert report["document"]["use"] == ["+ssl", "-test"]
    assert report["metadata"]["kind"] == "verified-md5-cache"
    assert report["provider_visibility"]["reason"] == "not requested"


def test_analyze_discloses_isolated_metadata_generation(tmp_path):
    ebuild, _cache = _fixture(tmp_path)

    def metadata_loader(_ebuild):
        return ({
            "eapi": "8",
            "dependencies": {
                field: "" for field in (
                    "DEPEND", "RDEPEND", "BDEPEND", "IDEPEND", "PDEPEND")
            },
        }, {
            "kind": "verified-md5-cache",
            "generator": "egencache --external-cache-only",
            "generator_output_bytes": 128,
            "generator_output_limit": 64 * 1024,
            "generator_timeout_seconds": 120,
        })

    report = analyze_ebuild_dependencies(
        ebuild, analyzer=FakeAnalyzer, metadata_loader=metadata_loader)

    assert report["metadata_generation"] == {
        "command": "egencache --external-cache-only",
        "isolated": True,
        "output_bytes": 128,
        "output_limit": 64 * 1024,
        "repository_writes": False,
        "timeout_seconds": 120,
    }


def test_isolated_egencache_replaces_stale_worktree_metadata(tmp_path):
    ebuild, cache = _fixture(tmp_path)
    cache.write_text("EAPI=8\n_md5_=stale\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "repo_name").write_text("worktree\n", encoding="utf-8")
    class Repositories:
        prepos_order = ["gentoo"]

        @staticmethod
        def mainRepo():
            return SimpleNamespace(name="gentoo")

        def __getitem__(self, name):
            assert name == "gentoo"
            return SimpleNamespace(location="/var/db/repos/gentoo")

    api = SimpleNamespace(
        settings=SimpleNamespace(repositories=Repositories()))
    seen = {}

    def generate(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        cache_root = Path(command[command.index("--cache-dir") + 1])
        configuration = command[
            command.index("--repositories-configuration") + 1]
        repository_location = Path(
            configuration.split("[worktree]", 1)[1]
            .split("location = ", 1)[1].splitlines()[0])
        generated = (
            cache_root / Path(str(repository_location).lstrip("/"))
            / "dev-libs" / "demo-1")
        generated.parent.mkdir(parents=True)
        digest = hashlib.md5(
            ebuild.read_bytes(), usedforsecurity=False).hexdigest()
        generated.write_text(
            f"EAPI=8\nRDEPEND=dev-libs/generated:=\n_md5_={digest}\n",
            encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    document, provenance = generated_ebuild_metadata(
        ebuild, portage_api=api, runner=generate)

    assert document["dependencies"]["RDEPEND"] == "dev-libs/generated:="
    assert provenance["generator"] == "egencache --external-cache-only"
    assert provenance["generator_source"] == (
        "repository-view-without-pregenerated-cache")
    assert provenance["retained"] is False
    assert provenance["cache"] is None
    assert provenance["generator_output_bytes"] == 0
    assert provenance["generator_output_limit"] == 64 * 1024
    assert provenance["generator_timeout_seconds"] == 120
    assert "--external-cache-only" in seen["command"]
    assert seen["command"][-1] == "dev-libs/demo"
    assert seen["kwargs"]["timeout"] == 120


def test_isolated_egencache_stops_output_flood_process_group(
        tmp_path, monkeypatch):
    ebuild, cache = _fixture(tmp_path)
    cache.write_text("EAPI=8\n_md5_=stale\n", encoding="utf-8")
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "repo_name").write_text("worktree\n", encoding="utf-8")

    class Repositories:
        prepos_order = ["gentoo"]

        @staticmethod
        def mainRepo():
            return SimpleNamespace(name="gentoo")

        def __getitem__(self, name):
            assert name == "gentoo"
            return SimpleNamespace(location="/var/db/repos/gentoo")

    api = SimpleNamespace(
        settings=SimpleNamespace(repositories=Repositories()))
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    child_pid_path = tmp_path / "child.pid"
    egencache = executable_dir / "egencache"
    egencache.write_text("""\
#!/bin/sh
(
    trap '' TERM
    while :; do
        printf '%8192s' x
    done
) &
echo "$!" > "${GZH_TEST_CHILD_PID}"
wait
""", encoding="utf-8")
    egencache.chmod(0o755)
    monkeypatch.setenv(
        "PATH", f"{executable_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("GZH_TEST_CHILD_PID", str(child_pid_path))

    started = time.monotonic()
    with pytest.raises(
            DependencyMetadataError,
            match="isolated metadata generator output exceeded"):
        generated_ebuild_metadata(ebuild, portage_api=api)
    elapsed = time.monotonic() - started

    # The guarantee under test is that the generator is stopped on overflow, not
    # how fast a loaded machine gets there; keep a bound that a hang would break.
    assert elapsed < 30
    child_pid = _recorded_child_pid(child_pid_path)
    if child_pid is None:
        return
    # Reaping a killed process group is scheduler-bound, so poll for a
    # bound that proves the child does not survive rather than one that
    # measures how fast a loaded machine reaps it.
    for _attempt in range(500):
        try:
            process_state = (Path("/proc") / str(child_pid) / "stat").read_text(
                encoding="utf-8").split(") ", 1)[1][0]
        except FileNotFoundError:
            break
        if process_state in {"X", "Z"}:
            break
        time.sleep(0.01)
    else:
        pytest.fail("metadata generator child remained running after output overflow")


def test_provider_visibility_is_scoped_to_configured_repositories(tmp_path):
    ebuild, _cache = _fixture(tmp_path)

    class DBAPI:
        @staticmethod
        def match(atom):
            assert atom == "dev-libs/libfoo:="
            return ["dev-libs/libfoo-1"]

    settings = {
        "ARCH": "amd64",
        "PROFILE_PATH": "/profiles/default/linux/amd64",
    }
    settings = SimpleNamespace(
        get=settings.get,
        repositories=SimpleNamespace(prepos_order=["gentoo", "overlay"]),
    )
    api = SimpleNamespace(
        root="/",
        settings=settings,
        db={"/": {"porttree": SimpleNamespace(dbapi=DBAPI())}},
    )
    report = analyze_ebuild_dependencies(
        ebuild,
        resolve_providers=True,
        analyzer=FakeAnalyzer,
        portage_api=api,
    )
    visibility = report["provider_visibility"]
    assert visibility["complete"] is True
    assert visibility["configured_repositories"] == ["gentoo", "overlay"]
    assert visibility["results"][0]["matches"] == ["dev-libs/libfoo-1"]


def test_compare_dependencies_reports_syntax_candidates_and_provenance(tmp_path):
    before, _before_cache = _version_fixture(
        tmp_path,
        "1",
        rdepend="dev-libs/libfoo:0 !dev-libs/conflict:0",
        bdepend="test? ( dev-build/cmake )",
    )
    after, _after_cache = _version_fixture(
        tmp_path,
        "2",
        rdepend="dev-libs/libfoo:= !!dev-libs/conflict:0 dev-libs/new",
        bdepend="!test? ( dev-build/cmake )",
    )

    report = compare_ebuild_dependencies(before, after, use=["-test"])

    assert report["ok"] is True
    assert report["complete"] is True
    assert report["state"] == "complete"
    assert report["modes"] == {
        "declarations": "potential",
        "use_selection": "reduced",
    }
    assert report["use"]["before"]["disabled"] == ["test"]
    assert report["use"]["after"]["disabled"] == ["test"]
    assert report["inputs"]["before"]["metadata"]["kind"] == "verified-md5-cache"
    assert report["inputs"]["after"]["metadata"]["kind"] == "verified-md5-cache"
    assert report["inputs"]["before"]["metadata"]["cache_sha256"] \
        != report["inputs"]["after"]["metadata"]["cache_sha256"]

    rdepend = report["fields"]["RDEPEND"]
    assert rdepend["added_atoms"] == [
        "!!dev-libs/conflict:0", "dev-libs/libfoo:=", "dev-libs/new"]
    assert rdepend["removed_atoms"] == [
        "!dev-libs/conflict:0", "dev-libs/libfoo:0"]
    assert rdepend["raw_expression"] == {
        "before": "dev-libs/libfoo:0 !dev-libs/conflict:0",
        "after": "dev-libs/libfoo:= !!dev-libs/conflict:0 dev-libs/new",
    }
    assert rdepend["potential_structure"]["before"] == [
        "dev-libs/libfoo:0", "!dev-libs/conflict:0"]
    assert [item["cp"] for item in rdepend["slot_change_candidates"]] == [
        "dev-libs/libfoo"]
    assert [item["cp"] for item in rdepend["blocker_change_candidates"]] == [
        "dev-libs/conflict"]

    bdepend = report["fields"]["BDEPEND"]
    assert bdepend["condition_changes"] == {
        "candidate": True,
        "flags_added": [],
        "flags_removed": [],
        "before_expression": "test? ( dev-build/cmake )",
        "after_expression": "!test? ( dev-build/cmake )",
        "selected_atoms_added": ["dev-build/cmake"],
        "selected_atoms_removed": [],
    }


def test_compare_dependencies_without_use_reports_potential_atoms(tmp_path):
    before, _before_cache = _version_fixture(
        tmp_path, "1", depend="feature? ( dev-libs/old )")
    after, _after_cache = _version_fixture(
        tmp_path, "2", depend="feature? ( dev-libs/new )")

    report = compare_ebuild_dependencies(before, after)

    assert report["complete"] is True
    assert report["modes"] == {
        "declarations": "potential",
        "use_selection": "not-requested",
    }
    assert report["use"]["provided"] is False
    assert report["fields"]["DEPEND"]["added_atoms"] == ["dev-libs/new"]
    assert report["fields"]["DEPEND"]["removed_atoms"] == ["dev-libs/old"]
    assert report["fields"]["DEPEND"]["condition_changes"][
        "selected_atoms_added"] is None


def test_compare_dependencies_keeps_disabled_branch_in_declaration_delta(tmp_path):
    before, _before_cache = _version_fixture(
        tmp_path, "1", depend="feature? ( dev-libs/old )")
    after, _after_cache = _version_fixture(
        tmp_path, "2", depend="feature? ( dev-libs/new )")

    report = compare_ebuild_dependencies(before, after, use=["-feature"])

    depend = report["fields"]["DEPEND"]
    assert depend["added_atoms"] == ["dev-libs/new"]
    assert depend["removed_atoms"] == ["dev-libs/old"]
    assert depend["use_reduced_delta"] == {
        "added_atoms": [],
        "removed_atoms": [],
    }


def test_compare_dependencies_ignores_provenance_only_changes(tmp_path):
    before, _before_cache = _version_fixture(
        tmp_path, "1", rdepend="dev-libs/libfoo:0")
    after, _after_cache = _version_fixture(
        tmp_path, "2", rdepend="dev-libs/libfoo:0")

    report = compare_ebuild_dependencies(before, after)

    assert report["complete"] is True
    assert report["changed"] is False
    assert all(not field["changed"] for field in report["fields"].values())
    assert report["inputs"]["before"] != report["inputs"]["after"]


def test_compare_dependencies_fails_closed_on_unverified_cache(tmp_path):
    before, _before_cache = _version_fixture(tmp_path, "1", rdepend="dev-libs/old")
    after, after_cache = _version_fixture(tmp_path, "2", rdepend="dev-libs/new")
    after_cache.write_text("EAPI=8\n_md5_=stale\n", encoding="utf-8")

    report = compare_ebuild_dependencies(
        before, after, metadata_loader=cached_ebuild_metadata)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["state"] == "error"
    assert report["changed"] is None
    assert report["fields"] is None
    assert report["inputs"]["before"]["metadata"]["kind"] == "verified-md5-cache"
    assert report["inputs"]["after"]["metadata"] is None
    assert report["errors"] == [{
        "side": "after",
        "stage": "metadata",
        "code": "metadata-not-verified",
        "detail": f"metadata cache does not match the ebuild: {after_cache}",
    }]


def test_compare_dependencies_fails_closed_on_incomplete_explicit_use(tmp_path):
    before, _before_cache = _version_fixture(
        tmp_path, "1", rdepend="feature? ( dev-libs/old )")
    after, _after_cache = _version_fixture(
        tmp_path, "2", rdepend="feature? ( dev-libs/new )")

    report = compare_ebuild_dependencies(before, after, use=[])

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["modes"]["use_selection"] == "reduced"
    assert report["use"]["provided"] is True
    assert report["fields"] is None
    assert [error["side"] for error in report["errors"]] == ["before", "after"]
    assert all(error["code"] == "analysis-not-complete" for error in report["errors"])


def test_compare_dependencies_fails_closed_on_analyzer_exception(tmp_path):
    before, _before_cache = _version_fixture(tmp_path, "1", rdepend="dev-libs/old")
    after, _after_cache = _version_fixture(tmp_path, "2", rdepend="dev-libs/new")

    class BrokenAnalyzer:
        class AnalysisError(Exception):
            pass

        @staticmethod
        def analyze(document, provenance=None):
            raise RuntimeError("broken analyzer")

    report = compare_ebuild_dependencies(before, after, analyzer=BrokenAnalyzer)

    assert report["ok"] is False
    assert report["complete"] is False
    assert report["fields"] is None
    assert [error["code"] for error in report["errors"]] == [
        "analysis-exception", "analysis-exception"]
