import difflib
import json
from pathlib import Path

from click.testing import CliRunner

import gzh.cli as cli_mod
from gzh.bump import (bump_scaffold, diff_ebuild, highest_ebuild,
                      refresh_copyright_year, resolve_package_directory)
from gzh.bump_plan import build_bump_plan


def _pkgdir(tmp_path: Path) -> Path:
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.0.ebuild").write_text('EAPI=8\nDESCRIPTION="x"\nPV_OLD="1.0.0"\n')
    (d / "foo-1.1.0.ebuild").write_text('EAPI=8\nDESCRIPTION="x"\nPV_OLD="1.1.0"\n')
    return d


def test_highest_ebuild(tmp_path):
    d = _pkgdir(tmp_path)
    assert highest_ebuild(d, "foo").name == "foo-1.1.0.ebuild"


def test_highest_ebuild_orders_by_vercmp_not_filename(tmp_path):
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    for pv in ("1.9", "1.10"):
        (d / f"foo-{pv}.ebuild").write_text("EAPI=8\n")
    assert highest_ebuild(d, "foo").name == "foo-1.10.ebuild"


def test_highest_ebuild_skips_live(tmp_path):
    """A live ebuild has no SRC_URI or KEYWORDS, so it is not a bump template."""
    d = tmp_path / "net-proxy" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.ebuild").write_text("EAPI=8\n")
    (d / "foo-9999.ebuild").write_text("EAPI=8\nEGIT_REPO_URI=x\n")
    assert highest_ebuild(d, "foo").name == "foo-1.0.ebuild"


def test_bump_scaffold_refuses_live_only(tmp_path):
    d = tmp_path / "net-proxy" / "foo"
    d.mkdir(parents=True)
    (d / "foo-9999.ebuild").write_text("EAPI=8\nEGIT_REPO_URI=x\n")
    try:
        bump_scaffold(d, "foo", "1.0")
    except FileNotFoundError as exc:
        assert "live-only" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError for a live-only package")


def test_bump_scaffold_copies_highest(tmp_path):
    d = _pkgdir(tmp_path)
    new = bump_scaffold(d, "foo", "1.2.0")
    assert new.name == "foo-1.2.0.ebuild"
    assert new.exists()
    # content copied from highest (1.1.0), filename changed
    assert 'PV_OLD="1.1.0"' in new.read_text()


def test_bump_scaffold_rejects_invalid_or_live_version(tmp_path):
    d = _pkgdir(tmp_path)
    for version in ("../2", "9999"):
        try:
            bump_scaffold(d, "foo", version)
        except ValueError as exc:
            assert "released Gentoo version" in str(exc)
        else:
            raise AssertionError(f"expected version {version!r} to be rejected")


def test_bump_scaffold_rejects_equal_or_lower_version(tmp_path):
    d = _pkgdir(tmp_path)
    for version in ("1.1.0", "1.0.9"):
        try:
            bump_scaffold(d, "foo", version)
        except ValueError as exc:
            assert "must be greater" in str(exc)
        else:
            raise AssertionError(f"expected version {version!r} to be rejected")


def test_build_bump_plan_is_read_only_and_pins_source(tmp_path):
    d = _pkgdir(tmp_path)
    report = build_bump_plan(
        tmp_path, "dev-python/foo", "1.2.0", package_model="source")

    assert report["ok"] is True
    assert report["current_version"] == "1.1.0"
    assert report["retention"]["decision"] == "review-required"
    assert len(report["actions"][0]["source_sha256"]) == 64
    assert not (d / "foo-1.2.0.ebuild").exists()


def _asset_evidence(tmp_path, *, decisions=None):
    path = tmp_path / "release-assets.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "previous": {
            "version": "1.1.0", "release_url": "https://example/1.1.0",
            "complete": True,
            "assets": [{"filename": "foo-amd64.deb", "architecture": "amd64"}],
        },
        "current": {
            "version": "1.2.0", "release_url": "https://example/1.2.0",
            "complete": True,
            "assets": [
                {"filename": "foo-x86_64.deb", "architecture": "amd64"},
                {"filename": "foo-arm64.deb", "architecture": "arm64"},
            ],
        },
        "decisions": decisions or {},
    }), encoding="utf-8")
    return path


def test_prebuilt_plan_requires_complete_release_asset_evidence(tmp_path):
    package = tmp_path / "app-misc" / "foo-bin"
    package.mkdir(parents=True)
    (package / "foo-bin-1.1.0.ebuild").write_text(
        'EAPI=8\nSRC_URI="https://example/foo-amd64.deb"\n', encoding="utf-8")

    report = build_bump_plan(
        tmp_path, "app-misc/foo-bin", "1.2.0", package_model="prebuilt")

    assert report["ok"] is False
    assert report["can_apply"] is False
    assert report["release_assets"]["required"] is True


def test_prebuilt_plan_records_every_architecture_and_asset_decision(tmp_path):
    package = tmp_path / "app-misc" / "foo-bin"
    package.mkdir(parents=True)
    (package / "foo-bin-1.1.0.ebuild").write_text(
        'EAPI=8\nSRC_URI="https://example/foo-amd64.deb"\n', encoding="utf-8")
    incomplete = build_bump_plan(
        tmp_path, "app-misc/foo-bin", "1.2.0",
        package_model="prebuilt",
        assets_evidence=_asset_evidence(tmp_path))
    decisions = {
        "architecture-added:arm64": "add the published arm64 artifact and keyword",
        "assets-changed:amd64": "update the amd64 SRC_URI filename",
    }
    complete = build_bump_plan(
        tmp_path, "app-misc/foo-bin", "1.2.0",
        package_model="prebuilt",
        assets_evidence=_asset_evidence(tmp_path, decisions=decisions))

    assert incomplete["can_apply"] is False
    assert {item["id"] for item in incomplete["release_assets"]["changes"]} == {
        "architecture-added:arm64", "assets-changed:amd64"}
    assert complete["ok"] is True
    assert complete["release_assets"]["decisions_complete"] is True


def test_plan_requires_explicit_package_model(tmp_path):
    _pkgdir(tmp_path)

    try:
        build_bump_plan(tmp_path, "dev-python/foo", "1.2.0")
    except TypeError as exc:
        assert "package_model" in str(exc)
    else:
        raise AssertionError("expected package_model to be required")


def test_ambiguous_package_can_be_explicitly_classified_prebuilt(tmp_path):
    package = tmp_path / "app-misc" / "foo"
    package.mkdir(parents=True)
    (package / "foo-1.1.0.ebuild").write_text(
        'EAPI=8\nSRC_URI="https://example/foo.tar.gz"\n', encoding="utf-8")

    report = build_bump_plan(
        tmp_path, "app-misc/foo", "1.2.0", package_model="prebuilt")

    assert report["package_model"]["state"] == "classified"
    assert report["package_model"]["detected_prebuilt_indicators"] == []
    assert report["release_assets"]["required"] is True
    assert report["can_apply"] is False


def test_source_model_rejects_deterministic_prebuilt_indicators(tmp_path):
    cases = (
        ("foo-bin", 'SRC_URI="https://example/foo.tar.gz"', "package-name:-bin"),
        ("foo", 'QA_PREBUILT="usr/bin/foo"', "ebuild-variable:QA_PREBUILT"),
        ("foo", 'SRC_URI="https://example/foo.AppImage"',
         "src-uri:binary-container"),
        ("foo", 'SRC_URI="https://example/foo.deb"',
         "src-uri:binary-container"),
        ("foo", 'SRC_URI="https://example/foo.rpm"',
         "src-uri:binary-container"),
        ("foo", 'inherit rpm\nSRC_URI="https://example/${P}.asset"',
         "ebuild-eclass:rpm"),
        ("foo", 'SRC_URI="https://example/foo.jar"', "src-uri:jvm-bytecode"),
        ("foo", 'SRC_URI="https://example/foo-amd64.tar.xz"',
         "src-uri:architecture-specific-archive"),
        ("foo", 'SRC_URI="https://example/install.sh"',
         "src-uri:standalone-bundle"),
        ("foo", "SRC_URI=https://example/install.sh",
         "src-uri:standalone-bundle"),
    )
    for index, (name, body, expected) in enumerate(cases):
        root = tmp_path / str(index)
        package = root / "app-misc" / name
        package.mkdir(parents=True)
        (package / f"{name}-1.1.0.ebuild").write_text(
            f"EAPI=8\n{body}\n", encoding="utf-8")

        report = build_bump_plan(
            root, f"app-misc/{name}", "1.2.0", package_model="source")

        assert report["package_model"]["state"] == "conflict"
        assert expected in report["package_model"]["detected_prebuilt_indicators"]
        assert report["release_assets"]["required"] is True
        assert report["can_apply"] is False


def test_prebuilt_indicator_follows_simple_src_uri_variable(tmp_path):
    package = tmp_path / "app-misc" / "foo"
    package.mkdir(parents=True)
    (package / "foo-1.1.0.ebuild").write_text(
        'EAPI=8\nMY_ASSET="foo-amd64.deb"\nSRC_URI="https://example/${MY_ASSET}"\n',
        encoding="utf-8")

    report = build_bump_plan(
        tmp_path, "app-misc/foo", "1.2.0", package_model="source")

    assert "src-uri:binary-container" in report[
        "package_model"]["detected_prebuilt_indicators"]
    assert report["can_apply"] is False


def test_source_model_ignores_source_built_output_names(tmp_path):
    package = tmp_path / "dev-java" / "foo"
    package.mkdir(parents=True)
    (package / "foo-1.1.0.ebuild").write_text(
        'EAPI=8\nSRC_URI="https://example/${P}.tar.gz"\n'
        'src_install() { java-pkg_dojar target/foo.jar || die; }\n',
        encoding="utf-8")

    report = build_bump_plan(
        tmp_path, "dev-java/foo", "1.2.0", package_model="source")

    assert report["package_model"]["state"] == "classified"
    assert report["package_model"]["detected_prebuilt_indicators"] == []
    assert report["can_apply"] is True


def test_prebuilt_indicators_ignore_comment_only_lines(tmp_path):
    package = tmp_path / "app-misc" / "foo"
    package.mkdir(parents=True)
    (package / "foo-1.1.0.ebuild").write_text(
        'EAPI=8\n# Old upstream asset was foo-amd64.deb\n'
        'SRC_URI="https://example/foo.tar.gz"\n',
        encoding="utf-8")

    report = build_bump_plan(
        tmp_path, "app-misc/foo", "1.2.0", package_model="source")

    assert report["package_model"]["state"] == "classified"
    assert report["package_model"]["detected_prebuilt_indicators"] == []
    assert report["can_apply"] is True


def test_source_model_rejects_unused_asset_evidence(tmp_path):
    _pkgdir(tmp_path)

    try:
        build_bump_plan(
            tmp_path, "dev-python/foo", "1.2.0", package_model="source",
            assets_evidence=_asset_evidence(tmp_path))
    except ValueError as exc:
        assert "prebuilt package model" in str(exc)
    else:
        raise AssertionError("expected source model asset evidence to be rejected")


def test_resolve_package_directory_rejects_traversal_and_missing(tmp_path):
    _pkgdir(tmp_path)
    for cat_pkg in ("../outside", "dev-python/missing", "dev-python/foo/extra"):
        try:
            resolve_package_directory(tmp_path, cat_pkg)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {cat_pkg!r} to be rejected")


def test_resolve_package_directory_rejects_symlink_outside_overlay(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    category = tmp_path / "dev-python"
    category.mkdir()
    (category / "foo").symlink_to(outside, target_is_directory=True)
    try:
        resolve_package_directory(tmp_path, "dev-python/foo")
    except ValueError:
        pass
    else:
        raise AssertionError("expected an escaping package symlink to be rejected")


def test_bump_scaffold_cli_rejects_traversal_before_write(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_mod, "find_overlay_root", lambda: tmp_path)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)

    result = CliRunner().invoke(
        cli_mod.cli, ["bump-scaffold", "../outside", "2"])

    assert result.exit_code == 1
    assert "invalid category/package" in result.output
    assert list(outside.iterdir()) == []


def test_diff_ebuild(tmp_path):
    d = _pkgdir(tmp_path)
    old = d / "foo-1.0.0.ebuild"
    new = d / "foo-1.1.0.ebuild"
    diff = diff_ebuild(old, new)
    assert "PV_OLD" in diff
    assert diff.startswith("---")


def _write(tmp_path, first_line):
    eb = tmp_path / "foo-1.0.ebuild"
    eb.write_text(f"{first_line}\n# Distributed under the terms of the GNU GPL v2\n\nEAPI=8\n")
    return eb


def test_refresh_copyright_extends_a_range(tmp_path):
    eb = _write(tmp_path, "# Copyright 1999-2024 Gentoo Authors")
    assert refresh_copyright_year(eb, year=2026) is True
    assert eb.read_text().startswith("# Copyright 1999-2026 Gentoo Authors\n")


def test_refresh_copyright_turns_a_single_year_into_a_range(tmp_path):
    eb = _write(tmp_path, "# Copyright 2024 Gentoo Authors")
    assert refresh_copyright_year(eb, year=2026) is True
    assert eb.read_text().startswith("# Copyright 2024-2026 Gentoo Authors\n")


def test_refresh_copyright_leaves_the_current_year_alone(tmp_path):
    eb = _write(tmp_path, "# Copyright 2026 Gentoo Authors")
    assert refresh_copyright_year(eb, year=2026) is False


def test_refresh_copyright_keeps_a_custom_holder(tmp_path):
    eb = _write(tmp_path, "# Copyright 2020-2024 Some Person")
    refresh_copyright_year(eb, year=2026)
    assert eb.read_text().startswith("# Copyright 2020-2026 Some Person\n")


def test_refresh_copyright_ignores_an_unrecognized_first_line(tmp_path):
    eb = _write(tmp_path, "# not a copyright line")
    assert refresh_copyright_year(eb, year=2026) is False
    assert eb.read_text().startswith("# not a copyright line\n")


def test_bump_scaffold_refreshes_the_copied_copyright_year(tmp_path):
    from datetime import date
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.0.ebuild").write_text("# Copyright 1999-2024 Gentoo Authors\nEAPI=8\n")
    new = bump_scaffold(d, "foo", "1.1.0")
    assert new.read_text().startswith(f"# Copyright 1999-{date.today().year} Gentoo Authors\n")
