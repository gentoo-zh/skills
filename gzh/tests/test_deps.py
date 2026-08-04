import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from gzh.deps import (
    DependencyMetadataError,
    MAX_METADATA_BYTES,
    analyze_ebuild_dependencies,
    cached_ebuild_metadata,
    compare_ebuild_dependencies,
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

    report = compare_ebuild_dependencies(before, after)

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
