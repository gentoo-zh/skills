import json
import subprocess
from pathlib import Path

import tomli_w

from gzh.upstream import NvcheckerProvider, PyPIProvider, get_latest_version


def _overlay(tmp_path: Path) -> Path:
    cfg = {
        "__config__": {"newver": "new.json", "oldver": "old.json"},
        "dev-python/foo": {"source": "github", "github": "x/foo",
                           "use_latest_release": True},
    }
    p = tmp_path / "overlay.toml"
    p.write_text(tomli_w.dumps(cfg))
    return p


def test_nvchecker_provider_reads_newver(monkeypatch, tmp_path):
    overlay = _overlay(tmp_path)

    def fake_run(args, **kw):
        # nvchecker --logger json emits JSON-lines on stdout
        out = json.dumps({"name": "dev-python/foo", "event": "updated",
                          "version": "1.2.3"})
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr("gzh.upstream.subprocess.run", fake_run)
    prov = NvcheckerProvider(overlay)
    assert prov.latest("dev-python/foo") == "1.2.3"
    assert prov.latest("dev-python/missing") is None  # no overlay.toml entry


def test_pypi_provider_via_http(monkeypatch):
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"info": {"version": "9.9.9"}}

    monkeypatch.setattr("gzh.upstream.httpx.get", lambda *a, **k: _Resp())
    assert PyPIProvider().latest("dev-python/foo") == "9.9.9"


def test_get_latest_prefers_nvchecker(monkeypatch, tmp_path):
    overlay = _overlay(tmp_path)

    def fake_run(args, **kw):
        out = json.dumps({"name": "dev-python/foo", "event": "updated",
                          "version": "2.0.0"})
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr("gzh.upstream.subprocess.run", fake_run)
    res = get_latest_version("dev-python/foo", overlay.parent,
                             overlay_toml=overlay)
    assert res["upstream"] == "2.0.0"
    assert res["source"] == "nvchecker"


def test_get_latest_falls_back_to_pypi(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay.toml"
    overlay.write_text('[__config__]\nnewver = "n.json"\n')  # no entry for pkg

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"info": {"version": "3.1.0"}}

    monkeypatch.setattr("gzh.upstream.httpx.get", lambda *a, **k: _Resp())
    res = get_latest_version("dev-python/foo", tmp_path, overlay_toml=overlay)
    assert res["upstream"] == "3.1.0"
    assert res["source"] == "pypi"
    assert res["advisory"] is not None  # suggest adding overlay.toml entry
