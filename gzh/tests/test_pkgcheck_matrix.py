import subprocess

import pytest

from gzh.pkgcheck import run_pkgcheck


def _successful_runner(calls):
    def fake(args, **kwargs):
        calls.append(args)
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="pkgcheck 0.test\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    return fake


def test_default_scope_preserves_existing_command(tmp_path):
    calls = []

    report = run_pkgcheck(tmp_path, runner=_successful_runner(calls))

    assert calls == [
        ["pkgcheck", "--version"],
        [
            "pkgcheck", "scan", "-R", "JsonStream", "--exit", "warning",
            str(tmp_path),
        ],
    ]
    assert report["requested_scope"] == {"arches": [], "profiles": []}


def test_selected_profiles_and_arches_use_official_selectors(tmp_path):
    calls = []

    report = run_pkgcheck(
        tmp_path,
        min_severity="error",
        net=True,
        profiles=("stable", "-exp", "default/linux/amd64/23.0"),
        arches=("amd64", "arm64", "-x86"),
        runner=_successful_runner(calls),
    )

    assert calls[1] == [
        "pkgcheck", "scan", "-R", "JsonStream", "--exit", "error",
        "--arches=amd64,arm64,-x86",
        "--profiles=stable,-exp,default/linux/amd64/23.0",
        "--net",
        str(tmp_path),
    ]
    assert report["requested_scope"] == {
        "arches": ["amd64", "arm64", "-x86"],
        "profiles": ["stable", "-exp", "default/linux/amd64/23.0"],
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("profiles", "stable"),
        ("profiles", ("",)),
        ("profiles", ("stable,exp",)),
        ("profiles", ("default/linux/../amd64",)),
        ("profiles", (" default/linux/amd64",)),
        ("profiles", ("--stable",)),
        ("arches", "amd64"),
        ("arches", ("",)),
        ("arches", ("amd64,arm64",)),
        ("arches", ("~amd64",)),
        ("arches", ("-",)),
        ("arches", (None,)),
    ],
)
def test_invalid_selectors_fail_before_execution(tmp_path, field, value):
    calls = []
    kwargs = {field: value}

    with pytest.raises(ValueError, match=field):
        run_pkgcheck(tmp_path, runner=_successful_runner(calls), **kwargs)

    assert calls == []


def test_version_failure_retains_scope_and_marks_report_incomplete(tmp_path):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(args, 2, stdout="", stderr="failed\n")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    report = run_pkgcheck(
        tmp_path, profiles=("all",), arches=("amd64",), runner=fake)

    assert report["ok"] is True
    assert report["complete"] is False
    assert report["state"] == "incomplete"
    assert report["requested_scope"] == {
        "arches": ["amd64"],
        "profiles": ["all"],
    }
    assert report["errors"][0]["stage"] == "tool-version"
    assert calls[1][-3:] == ["--arches=amd64", "--profiles=all", str(tmp_path)]


def test_selected_scope_survives_timeout_and_truncation(tmp_path):
    def fake(args, **kwargs):
        if args == ["pkgcheck", "--version"]:
            return subprocess.CompletedProcess(
                args, 0, stdout="pkgcheck 0.test\n", stderr="")
        raise subprocess.TimeoutExpired(
            args, kwargs["timeout"], output="partial", stderr="still running")

    report = run_pkgcheck(
        tmp_path,
        profiles=("dev", "-deprecated"),
        arches=("arm64",),
        runner=fake,
        timeout=1,
        max_output_bytes=256,
    )

    assert report["complete"] is False
    assert report["truncated"] is True
    assert report["timed_out"] is True
    assert report["requested_scope"] == {
        "arches": ["arm64"],
        "profiles": ["dev", "-deprecated"],
    }
    assert report["command"][-3:] == [
        "--arches=arm64", "--profiles=dev,-deprecated", str(tmp_path),
    ]


def test_exact_argument_order_with_profiles_only(tmp_path):
    calls = []

    run_pkgcheck(
        tmp_path,
        profiles=("stable", "default/linux/amd64/23.0"),
        runner=_successful_runner(calls),
    )

    assert calls[1] == [
        "pkgcheck", "scan", "-R", "JsonStream", "--exit", "warning",
        "--profiles=stable,default/linux/amd64/23.0",
        str(tmp_path),
    ]
