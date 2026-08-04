import subprocess

import pytest

import gzh.dependency_query as query_mod
from gzh.dependency_query import query_reverse_dependencies


VERSION = "pkgcore 0.12.35"
COMMAND = [
    "pquery",
    "--raw",
    "--ebuild-repos",
    "--cpv",
    "-R",
    "--restrict-revdep",
    "dev-libs/target",
]


def _runner(stdout="", stderr="", returncode=0):
    def fake(args, **kwargs):
        if args == ["pquery", "--version"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{VERSION}\n", stderr="")
        return subprocess.CompletedProcess(
            args, returncode, stdout=stdout, stderr=stderr)

    return fake


def test_collects_exact_reverse_dependency_versions_and_scope():
    seen = []

    def fake(args, **kwargs):
        seen.append((args, kwargs))
        if args == ["pquery", "--version"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{VERSION}\n", stderr="")
        stdout = (
            "dev-lang/example-1.2-r3::gentoo\n"
            "app-misc/consumer-9999::local-overlay\n"
        )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="advisory\n")

    report = query_reverse_dependencies("dev-libs/target", runner=fake)

    assert report["complete"] is True
    assert report["ok"] is True
    assert report["command"] == COMMAND
    assert seen[1][0] == COMMAND
    assert seen[1][1]["capture_output"] is True
    assert "shell" not in seen[1][1]
    assert report["tool"]["version"] == VERSION
    assert report["stderr"] == "advisory\n"
    assert report["scope"] == {
        "abi_correctness": False,
        "active_profile_resolution": False,
        "dependency_view": "raw",
        "relationship": "potential-direct-reverse-dependency",
        "repositories": "configured-ebuild-repositories",
        "transitive_resolution": False,
    }
    assert report["results"] == [
        {
            "atom": "=dev-lang/example-1.2-r3::gentoo",
            "category": "dev-lang",
            "cpv": "dev-lang/example-1.2-r3",
            "package": "example",
            "repository": "gentoo",
            "revision": "r3",
            "version": "1.2-r3",
        },
        {
            "atom": "=app-misc/consumer-9999::local-overlay",
            "category": "app-misc",
            "cpv": "app-misc/consumer-9999",
            "package": "consumer",
            "repository": "local-overlay",
            "revision": "r0",
            "version": "9999",
        },
    ]


def test_no_matches_is_complete_evidence():
    report = query_reverse_dependencies("dev-libs/target", runner=_runner())

    assert report["complete"] is True
    assert report["ok"] is True
    assert report["state"] == "complete"
    assert report["results"] == []


@pytest.mark.parametrize("atom", ["not-an-atom", " dev-libs/target", "dev libs/target"])
def test_invalid_atom_stops_before_pquery(atom):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        raise AssertionError("pquery must not run for invalid input")

    report = query_reverse_dependencies(atom, runner=fake)

    assert report["complete"] is False
    assert report["state"] == "invalid-input"
    assert report["errors"][0]["stage"] == "input"
    assert report["command"] is None
    assert calls == []


@pytest.mark.parametrize("failure", ["missing", "version-failed"])
def test_missing_pquery_or_failed_version_is_incomplete(failure):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        if failure == "missing":
            raise FileNotFoundError("pquery")
        return subprocess.CompletedProcess(args, 2, stdout="", stderr="version failed\n")

    report = query_reverse_dependencies("dev-libs/target", runner=fake)

    assert report["complete"] is False
    assert report["state"] == "tool-incomplete"
    assert report["tool"]["version"] is None
    assert report["errors"][0]["stage"] == "tool-version"
    assert calls == [["pquery", "--version"]]
    if failure == "version-failed":
        assert report["stderr"] == "version failed\n"


def test_timeout_preserves_partial_output_and_is_incomplete():
    def fake(args, **kwargs):
        if args == ["pquery", "--version"]:
            return subprocess.CompletedProcess(args, 0, stdout=f"{VERSION}\n", stderr="")
        raise subprocess.TimeoutExpired(
            args, kwargs["timeout"], output="partial\n", stderr="still running\n")

    report = query_reverse_dependencies(
        "dev-libs/target", runner=fake, timeout=1)

    assert report["complete"] is False
    assert report["timed_out"] is True
    assert report["truncated"] is True
    assert report["state"] == "timed-out"
    assert report["stdout"] == "partial\n"
    assert report["stderr"] == "still running\n"
    assert report["results"] == []


def test_output_limit_does_not_publish_partial_results():
    report = query_reverse_dependencies(
        "dev-libs/target",
        runner=_runner(
            stdout="dev-lang/example-1::gentoo\n" + "x" * 300,
            stderr="y" * 300,
        ),
        max_output_bytes=256,
    )

    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["state"] == "truncated"
    assert report["results"] == []
    assert (report["execution"]["stdout_bytes"]
            + report["execution"]["stderr_bytes"]) == 256


@pytest.mark.parametrize(
    "line",
    [
        "not-a-cpv::gentoo",
        "dev-lang/example-1",
        "dev-lang/example-1::bad repo",
        " dev-lang/example-1::gentoo",
        "dev-lang/example-1::gentoo::other",
    ],
)
def test_malformed_output_fails_closed(line):
    stdout = f"dev-lang/valid-1::gentoo\n{line}\n"
    report = query_reverse_dependencies(
        "dev-libs/target", runner=_runner(stdout=stdout))

    assert report["complete"] is False
    assert report["ok"] is False
    assert report["state"] == "malformed-output"
    assert report["results"] == []
    assert report["partial_results"][0]["cpv"] == "dev-lang/valid-1"
    assert report["malformed_output"][0]["line"] == 2
    assert report["malformed_output"][0]["preview"] == line
    assert len(report["malformed_output"][0]["sha256"]) == 64


def test_result_count_limit_fails_closed(monkeypatch):
    monkeypatch.setattr(query_mod, "MAX_RESULT_RECORDS", 2)
    stdout = (
        "dev-lang/one-1::gentoo\n"
        "dev-lang/two-1::gentoo\n"
        "dev-lang/three-1::gentoo\n"
    )

    report = query_reverse_dependencies(
        "dev-libs/target", runner=_runner(stdout=stdout))

    assert report["complete"] is False
    assert report["results"] == []
    assert len(report["partial_results"]) == 2
    assert "exceeds 2 records" in report["malformed_output"][0]["message"]


def test_nonzero_exit_preserves_diagnostics_without_parsing_stdout():
    report = query_reverse_dependencies(
        "dev-libs/target",
        runner=_runner(
            stdout="dev-lang/partial-1::gentoo\n",
            stderr="repository load failed\n",
            returncode=2,
        ),
    )

    assert report["complete"] is False
    assert report["state"] == "failed"
    assert report["execution"]["complete"] is True
    assert report["execution"]["returncode"] == 2
    assert report["stdout"] == "dev-lang/partial-1::gentoo\n"
    assert report["stderr"] == "repository load failed\n"
    assert report["results"] == []
    assert report["partial_results"] == []
