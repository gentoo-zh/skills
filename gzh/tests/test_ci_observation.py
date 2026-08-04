from gzh.ci_observation import observe_ci


HEAD = "a" * 40


def test_ci_observation_preserves_full_names_urls_counts_and_final_state():
    calls = []
    long_name = "emerge (amd64-desktop-systemd, package with full matrix name)"
    long_url = "https://github.example/actions/runs/123456789/jobs/987654321"

    def provider(repository, pr_number):
        calls.append((repository, pr_number))
        return {
            "complete": True,
            "state": "OPEN",
            "merged": True,
            "merged_at": "2026-08-04T02:30:00Z",
            "merge_commit_sha": "b" * 40,
            "head_sha": HEAD,
            "url": "https://github.example/pull/456",
            "checks_complete": True,
            "checks": [
                {"name": long_name, "url": long_url,
                 "status": "IN_PROGRESS", "conclusion": None},
                {"name": "pkgcheck", "url": "https://github.example/check/2",
                 "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }

    result = observe_ci(
        "gentoo-zh/overlay", 456, provider,
        observed_at="2026-08-04T03:00:00Z",
    )

    assert calls == [("gentoo-zh/overlay", 456)]
    assert result["head_sha"] == HEAD
    assert result["final_pr_state"] == "merged"
    assert result["checks_state"] == "incomplete"
    assert result["counts"]["total"] == 2
    assert result["counts"]["passed"] == 1
    assert result["counts"]["pending"] == 1
    matrix = next(check for check in result["checks"] if check["name"] == long_name)
    assert matrix["name"] == long_name
    assert matrix["url"] == long_url


def test_ci_observation_is_stably_sorted_and_reports_failed_counts():
    def provider(_repository, _pr_number):
        return {
            "complete": True,
            "state": "OPEN",
            "head_sha": HEAD,
            "url": "https://github.example/pull/1",
            "checks_complete": True,
            "checks": [
                {"name": "z-last", "url": "https://github.example/z",
                 "status": "COMPLETED", "conclusion": "FAILURE"},
                {"name": "a-first", "url": "https://github.example/a",
                 "status": "COMPLETED", "conclusion": "SUCCESS"},
            ],
        }

    result = observe_ci(
        "gentoo-zh/overlay", 1, provider, observed_at="now")
    assert [check["name"] for check in result["checks"]] == [
        "a-first", "z-last"]
    assert result["checks_state"] == "failed"
    assert result["counts"]["failed"] == 1
    assert result["complete"] is True


def test_truncated_or_missing_check_metadata_is_incomplete():
    def provider(_repository, _pr_number):
        return {
            "complete": True,
            "state": "OPEN",
            "head_sha": HEAD,
            "url": "https://github.example/pull/1",
            "checks_complete": False,
            "checks": [{"name": "pkgcheck", "status": "COMPLETED",
                        "conclusion": "SUCCESS"}],
        }

    result = observe_ci(
        "gentoo-zh/overlay", 1, provider, observed_at="now")
    assert result["checks_state"] == "incomplete"
    assert result["complete"] is False
    assert "has no URL" in result["errors"][0]
