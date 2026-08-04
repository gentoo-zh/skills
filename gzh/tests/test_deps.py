import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from gzh.deps import (
    DependencyMetadataError,
    analyze_ebuild_dependencies,
    cached_ebuild_metadata,
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
