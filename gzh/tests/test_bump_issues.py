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


from gzh.bump_issues import apply_filters, graphql_to_queue

NODES = [
    {"number": 10581,
     "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
     "body": "oldver: 1.0.39\nCC: @Linerre", "state": "OPEN",
     "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
     "comments": {"nodes": [
         {"author": {"login": "microcai"}, "body": "hi",
          "createdAt": "2026-07-01T00:00:00Z"}]}},
    {"number": 999, "title": "random nvchecker note", "body": "",
     "state": "OPEN", "url": "u", "comments": {"nodes": []}},
]


def test_graphql_to_queue_skips_unmatched():
    queue, skipped = graphql_to_queue(NODES)
    assert skipped == 1
    assert len(queue) == 1
    item = queue[0]
    assert item["issue"] == 10581
    assert item["cat_pkg"] == "media-fonts/sarasa-gothic"
    assert item["target_version"] == "1.0.40"
    assert item["oldver"] == "1.0.39"
    assert item["maintainer"] == "Linerre"
    assert item["state"] == "open"
    assert item["url"].endswith("/issues/10581")
    assert item["comments_truncated"] is False
    assert item["comments"][0]["author"] == "microcai"


def test_graphql_to_queue_no_comments_option():
    queue, _ = graphql_to_queue(NODES, with_comments=False)
    assert queue[0]["comments"] == []
    assert queue[0]["comments_truncated"] is False


def test_graphql_to_queue_truncates_over_50_comments():
    many = [{"author": {"login": "x"}, "body": "y", "createdAt": "z"}] * 51
    nodes = [{"number": 1, "title": "[nvchecker] a/b can be bump to 1",
              "body": "", "state": "OPEN", "url": "u", "comments": {"nodes": many}}]
    queue, _ = graphql_to_queue(nodes)
    assert len(queue[0]["comments"]) == 50
    assert queue[0]["comments_truncated"] is True


def test_apply_filters_by_maintainer_and_pkg():
    queue, _ = graphql_to_queue(NODES)
    assert len(apply_filters(queue, maintainer="Linerre")) == 1
    assert apply_filters(queue, maintainer="nobody") == []
    assert len(apply_filters(queue, pkg="media-fonts/sarasa-gothic")) == 1
    assert apply_filters(queue, pkg="x/y") == []
