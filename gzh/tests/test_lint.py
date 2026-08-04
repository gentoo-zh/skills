from gzh.lint import lint_ebuild


def _good():
    return {
        "EAPI": "8", "KEYWORDS": "~amd64 ~arm64", "LICENSE": "MIT",
        "SRC_URI": "https://x/${P}.tar.gz", "HOMEPAGE": "https://x",
        "DESCRIPTION": "x", "SLOT": "0", "PV": "1.0",
    }


def test_clean_ebuild_no_issues():
    assert lint_ebuild(_good()) == []


def test_eapi_9_is_supported():
    ok = _good()
    ok["EAPI"] = "9"
    assert not any(i["rule"] == "eapi-unsupported" for i in lint_ebuild(ok))


def test_stable_keyword_is_error():
    bad = _good()
    bad["KEYWORDS"] = "amd64 ~arm64"
    issues = lint_ebuild(bad)
    assert any(i["rule"] == "stable-keyword" and i["severity"] == "error" for i in issues)


def test_unsupported_eapi_is_error():
    bad = _good()
    bad["EAPI"] = "5"
    issues = lint_ebuild(bad)
    assert any(i["rule"] == "eapi-unsupported" for i in issues)


def test_missing_license_is_error():
    bad = _good()
    bad["LICENSE"] = ""
    issues = lint_ebuild(bad)
    assert any(i["rule"] == "missing-license" for i in issues)


def test_pypi_eclass_skips_missing_src_uri():
    bad = _good()
    bad["SRC_URI"] = ""
    bad["inherit"] = ["distutils-r1", "pypi"]
    issues = lint_ebuild(bad)
    assert not any(i["rule"] == "missing-src_uri" for i in issues)


def test_missing_src_uri_reported_without_eclass():
    bad = _good()
    bad["SRC_URI"] = ""
    issues = lint_ebuild(bad)
    assert any(i["rule"] == "missing-src_uri" for i in issues)


def test_liveup_skips_missing_src_uri():
    bad = _good()
    bad["SRC_URI"] = ""
    bad["PV"] = "9999"
    issues = lint_ebuild(bad)
    assert not any(i["rule"] == "missing-src_uri" for i in issues)


def test_unpacked_deb_bypass_is_a_review_finding():
    parsed = _good()
    parsed["inherit"] = ["unpacker"]
    source = """\
inherit unpacker

src_unpack() {
    ar x "${DISTDIR}/${A}"
}

src_install() {
    tar -xf "${WORKDIR}/data.tar.xz" -C "${D}" || die
}
"""

    issues = lint_ebuild(parsed, source_text=source)

    assert {(issue["rule"], issue["severity"]) for issue in issues} >= {
        ("unpacker-helper-bypassed", "warning"),
        ("archive-extraction-in-src-install", "warning"),
    }


def test_unpacked_deb_helper_avoids_bypass_finding():
    parsed = _good()
    parsed["inherit"] = ["unpacker"]
    source = """\
inherit unpacker

src_unpack() {
    unpack_deb "${DISTDIR}/${A}"
}

src_install() {
    doins -r usr
}
"""

    issues = lint_ebuild(parsed, source_text=source)

    assert not any(issue["rule"] == "unpacker-helper-bypassed" for issue in issues)
    assert not any(
        issue["rule"] == "archive-extraction-in-src-install" for issue in issues)


def test_non_debian_unpacker_override_does_not_claim_unpack_deb_is_required():
    parsed = _good()
    parsed["inherit"] = ["unpacker"]
    source = """\
inherit unpacker

src_unpack() {
    unpack_makeself "${A}"
}
"""

    issues = lint_ebuild(parsed, source_text=source)

    assert not any(issue["rule"] == "unpacker-helper-bypassed" for issue in issues)


def test_archive_extraction_in_src_install_does_not_require_unpacker():
    source = """\
src_install() {
    tar -xf payload.tar -C "${D}" || die
}
"""

    issues = lint_ebuild(_good(), source_text=source)

    assert any(
        issue["rule"] == "archive-extraction-in-src-install" for issue in issues)


def test_inline_comment_cannot_claim_unpack_deb_was_called():
    parsed = _good()
    parsed["inherit"] = ["unpacker"]
    source = """\
src_unpack() {
    ar x "${DISTDIR}/${A}" # unpack_deb
}
"""

    issues = lint_ebuild(parsed, source_text=source)

    assert any(issue["rule"] == "unpacker-helper-bypassed" for issue in issues)


def test_quoted_helper_name_cannot_claim_unpack_deb_was_called():
    parsed = _good()
    parsed["inherit"] = ["unpacker"]
    source = """\
src_unpack() {
    einfo "not using unpack_deb"
    ar x "${DISTDIR}/${A}"
}
"""

    issues = lint_ebuild(parsed, source_text=source)

    assert any(issue["rule"] == "unpacker-helper-bypassed" for issue in issues)


def test_additional_archive_extractors_in_src_install_are_review_findings():
    source = """\
src_install() {
    bsdtar -xf payload.tar -C "${D}"
    rpm2cpio payload.rpm | cpio -id
}
"""

    issues = lint_ebuild(_good(), source_text=source)

    assert sum(
        issue["rule"] == "archive-extraction-in-src-install"
        for issue in issues) == 1


def test_comments_and_quoted_extractor_names_are_not_commands():
    source = """\
src_install() {
    # bsdtar -xf payload.tar -C "${D}"
    einfo "rpm2cpio payload.rpm | cpio -id"
}
"""

    issues = lint_ebuild(_good(), source_text=source)

    assert not any(
        issue["rule"] == "archive-extraction-in-src-install" for issue in issues)
