import subprocess
from pathlib import Path

from gzh.pkgcheck import SEVERITIES, run_pkgcheck

# pkgcheck's JsonStream reporter emits NDJSON: one flat object per line, keyword name in
# __class__, and NO severity field. The pass/fail gate is pkgcheck's own --exit status.


def test_uses_jsonstream_reporter_and_exit_level(tmp_path):
    seen = {}

    def fake(args, **kw):
        seen["args"] = args
        out = ('{"__class__": "UnquotedVariable", "category": "dev-python", '
               '"package": "foo", "version": "1.0"}\n')
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    res = run_pkgcheck(tmp_path, min_severity="error", runner=fake)
    assert "-R" in seen["args"] and "JsonStream" in seen["args"]
    assert seen["args"][seen["args"].index("--exit") + 1] == "error"
    assert res["ok"] is True  # fake returncode 0
    assert res["results"][0]["code"] == "UnquotedVariable"  # __class__ mirrored to code


def test_ok_follows_pkgcheck_exit_status(tmp_path):
    def fake(args, **kw):
        # pkgcheck --exit returns non-zero when a gated finding exists
        return subprocess.CompletedProcess(
            args, 1,
            stdout='{"__class__":"NonexistentDeps","category":"x","package":"y","version":"1"}\n',
            stderr="")

    assert run_pkgcheck(tmp_path, runner=fake)["ok"] is False


def test_clean_scan(tmp_path):
    def fake(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_pkgcheck(tmp_path, runner=fake)
    assert res["ok"] is True
    assert res["results"] == []


def test_min_severity_maps_to_exit_with_fallback(tmp_path):
    seen = {}

    def fake(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_pkgcheck(tmp_path, min_severity="warning", runner=fake)
    assert seen["args"][seen["args"].index("--exit") + 1] == "warning"
    run_pkgcheck(tmp_path, min_severity="bogus", runner=fake)  # invalid -> warning
    assert seen["args"][seen["args"].index("--exit") + 1] == "warning"


def test_severities_ordered_high_to_low():
    assert SEVERITIES[0] == "error"
    assert SEVERITIES.index("warning") < SEVERITIES.index("info")
