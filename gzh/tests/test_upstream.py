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
        # args: [nvchecker, --file, <cfg>] ; read cfg to find newver path
        import tomllib
        cfg = tomllib.loads(Path(args[args.index("--file") + 1]).read_text())
        newver = Path(cfg["__config__"]["newver"])
        newver.write_text(json.dumps({"dev-python/foo": "1.2.3"}))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("gzh.upstream.subprocess.run", fake_run)
    prov = NvcheckerProvider(overlay)
    assert prov.latest("dev-python/foo") == "1.2.3"
    assert prov.latest("dev-python/missing") is None


def test_pypi_provider_via_http(monkeypatch):
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"info": {"version": "9.9.9"}}

    monkeypatch.setattr("gzh.upstream.httpx.get", lambda *a, **k: _Resp())
    assert PyPIProvider().latest("dev-python/foo") == "9.9.9"


def test_get_latest_prefers_nvchecker(monkeypatch, tmp_path):
    overlay = _overlay(tmp_path)
    monkeypatch.setattr(
        "gzh.upstream.subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="", stderr=""),
    )
    # make nvchecker write newver
    import gzh.upstream as up

    def fake_run(args, **kw):
        import tomllib
        cfg = tomllib.loads(Path(args[args.index("--file") + 1]).read_text())
        Path(cfg["__config__"]["newver"]).write_text(
            json.dumps({"dev-python/foo": "2.0.0"}))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
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
