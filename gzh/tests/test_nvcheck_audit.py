from gzh.nvcheck_audit import audit, infer_source


def test_infer_github_from_homepage():
    parsed = {"HOMEPAGE": "https://github.com/org/foo", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "github"
    assert entry == {"source": "github", "github": "org/foo", "use_latest_release": True}


def test_infer_github_strips_trailing_git_and_slash():
    parsed = {"HOMEPAGE": "https://github.com/org/foo.git", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert entry["github"] == "org/foo"


def test_infer_pypi_from_src_uri():
    parsed = {"HOMEPAGE": "https://example.org", "SRC_URI": "https://pypi.org/foo", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "pypi"
    assert entry == {"source": "pypi", "pypi": "foo"}


def test_infer_pypi_from_inherit():
    parsed = {"HOMEPAGE": "", "SRC_URI": "", "inherit": ["distutils-r1", "pypi"]}
    source, entry = infer_source(parsed, "foo")
    assert source == "pypi"
    assert entry["pypi"] == "foo"


def test_infer_git_from_gitlab_url():
    parsed = {"HOMEPAGE": "https://gitlab.com/org/foo", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "git"
    assert entry["source"] == "git"
    assert entry["use_max_tag"] is True


def test_infer_unknown_when_no_match():
    parsed = {"HOMEPAGE": "https://example.org", "SRC_URI": "https://example.org/foo.tar.gz", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "unknown"
    assert entry is None


def test_infer_github_priority_over_pypi():
    # both github homepage and pypi src_uri -> github wins
    parsed = {"HOMEPAGE": "https://github.com/org/foo", "SRC_URI": "https://pypi.org/foo", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "github"


def test_audit_stale_and_missing():
    configured = {"cat/a", "cat/b", "cat/removed"}
    actual = {"cat/a", "cat/b", "cat/new"}
    stale, missing = audit(configured, actual, filter_system=False)
    assert stale == ["cat/removed"]
    assert missing == ["cat/new"]


def test_audit_filters_system_packages_by_default():
    configured = {"cat/a"}
    actual = {"cat/a", "acct-group/x", "virtual/y", "cat/missing"}
    stale, missing = audit(configured, actual, filter_system=True)
    assert missing == ["cat/missing"]  # acct-group/virtual filtered


def test_audit_no_filter_includes_system():
    configured = {"cat/a"}
    actual = {"cat/a", "acct-group/x", "cat/missing"}
    _, missing = audit(configured, actual, filter_system=False)
    assert sorted(missing) == ["acct-group/x", "cat/missing"]


def test_audit_empty_when_consistent():
    configured = {"cat/a"}
    actual = {"cat/a"}
    stale, missing = audit(configured, actual)
    assert stale == [] and missing == []
