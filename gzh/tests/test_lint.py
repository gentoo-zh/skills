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
