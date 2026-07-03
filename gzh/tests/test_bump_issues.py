from gzh.bump_issues import parse_body, parse_title


def test_parse_title_typical():
    assert parse_title("[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40") == \
        ("media-fonts/sarasa-gothic", "1.0.40")


def test_parse_title_bin_and_version_suffix():
    assert parse_title("[nvchecker] net-proxy/naiveproxy-bin can be bump to 150.0.7871.63_p1") == \
        ("net-proxy/naiveproxy-bin", "150.0.7871.63_p1")


def test_parse_title_unmatched_returns_none():
    assert parse_title("some other title") is None
    assert parse_title("[nvchecker] not the bump pattern") is None


def test_parse_body_fields():
    assert parse_body("oldver: 1.0.39\nCC: @Linerre") == \
        {"oldver": "1.0.39", "maintainer": "Linerre"}


def test_parse_body_missing_fields():
    assert parse_body("nothing useful here") == \
        {"oldver": None, "maintainer": None}


def test_parse_body_cc_without_at_sign():
    assert parse_body("CC: someone")["maintainer"] == "someone"
