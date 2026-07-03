import json
import subprocess
from pathlib import Path

from gzh.pkgcheck import SEVERITY_ORDER, run_pkgcheck

SAMPLE = [
    {"cat": "dev-python", "package": "foo", "version": "1.0",
     "results": [
         {"code": "NonexistentDeps", "severity": "error", "msg": "bad dep"},
         {"code": "UnquotedVar", "severity": "warning", "msg": "unquoted"},
         {"code": "BogusVar", "severity": "style", "msg": "style nudge"},
     ]},
]


def test_pkgcheck_filters_by_severity(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(SAMPLE), stderr="")

    res = run_pkgcheck(tmp_path, min_severity="error", runner=fake_run)
    assert res["ok"] is False  # has an error
    codes = [r["code"] for r in res["results"]]
    assert "NonexistentDeps" in codes
    assert "UnquotedVar" not in codes  # filtered out (warning < error)


def test_pkgcheck_clean(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")
    res = run_pkgcheck(tmp_path, runner=fake_run)
    assert res["ok"] is True
    assert res["results"] == []


def test_severity_order():
    assert SEVERITY_ORDER["error"] > SEVERITY_ORDER["warning"]
    assert SEVERITY_ORDER["warning"] > SEVERITY_ORDER["style"]
