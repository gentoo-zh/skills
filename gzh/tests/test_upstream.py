import json
import subprocess
from pathlib import Path

import tomli_w

from gzh.upstream import (NvcheckerProvider, PyPIProvider, get_latest_version,
                          pypi_project)


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
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"info": {"version": "9.9.9"}}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("gzh.upstream.httpx.get", fake_get)
    assert PyPIProvider().latest("upstream-foo") == "9.9.9"
    assert seen["url"].endswith("/upstream-foo/json")


def test_pypi_provider_http_error_returns_none(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.HTTPError("404 not found")
    monkeypatch.setattr("gzh.upstream.httpx.get", boom)
    # a *-bin package is not on pypi; provider must not raise
    assert PyPIProvider().latest("lemminx") is None


def test_pypi_project_reads_structured_metadata(tmp_path):
    metadata = tmp_path / "dev-python" / "foo" / "metadata.xml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '<pkgmetadata><upstream><remote-id type="pypi">real-foo</remote-id>'
        '</upstream></pkgmetadata>')
    assert pypi_project(tmp_path, "dev-python/foo") == "real-foo"


def test_pypi_project_rejects_invalid_remote_id(tmp_path):
    metadata = tmp_path / "dev-python" / "foo" / "metadata.xml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '<pkgmetadata><upstream><remote-id type="pypi">../other</remote-id>'
        '</upstream></pkgmetadata>')
    assert pypi_project(tmp_path, "dev-python/foo") is None


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

    metadata = tmp_path / "dev-python" / "foo" / "metadata.xml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '<pkgmetadata><upstream><remote-id type="pypi">real-foo</remote-id>'
        '</upstream></pkgmetadata>')
    seen = {}

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"info": {"version": "3.1.0"}}

    def fake_get(url, **kwargs):
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr("gzh.upstream.httpx.get", fake_get)
    res = get_latest_version("dev-python/foo", tmp_path, overlay_toml=overlay)
    assert res["upstream"] == "3.1.0"
    assert res["source"] == "pypi"
    assert res["project"] == "real-foo"
    assert seen["url"].endswith("/real-foo/json")
    assert res["advisory"] is not None


def test_get_latest_does_not_guess_pypi_identity(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay.toml"
    overlay.write_text('[__config__]\nnewver = "n.json"\n')

    def unexpected(*args, **kwargs):
        raise AssertionError("PyPI must not be queried without a metadata remote-id")

    monkeypatch.setattr("gzh.upstream.httpx.get", unexpected)
    res = get_latest_version("app-misc/foo", tmp_path, overlay_toml=overlay)
    assert res["upstream"] is None
    assert res["source"] == "none"


def test_get_latest_does_not_fallback_when_tracker_returns_no_version(
        monkeypatch, tmp_path):
    overlay = _overlay(tmp_path)
    metadata = tmp_path / "dev-python" / "foo" / "metadata.xml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        '<pkgmetadata><upstream><remote-id type="pypi">real-foo</remote-id>'
        '</upstream></pkgmetadata>')
    monkeypatch.setattr(
        "gzh.upstream.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout='{"event":"up-to-date"}\n', stderr=""))
    monkeypatch.setattr(
        "gzh.upstream.httpx.get",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("configured tracker must not fall back")))
    res = get_latest_version("dev-python/foo", tmp_path, overlay_toml=overlay)
    assert res["upstream"] is None
    assert res["source"] == "nvchecker"
