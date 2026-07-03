from __future__ import annotations

import json
import subprocess
import tempfile
import tomllib
from pathlib import Path

import httpx
import tomli_w


class VersionProvider:
    def latest(self, cat_pkg: str) -> str | None:
        raise NotImplementedError


class NvcheckerProvider(VersionProvider):
    def __init__(self, overlay_toml: Path, keyfile: Path | None = None,
                 cmd: str = "nvchecker"):
        self.overlay_toml = Path(overlay_toml)
        self.keyfile = keyfile
        self.cmd = cmd

    def _entry(self, cat_pkg: str) -> dict | None:
        data = tomllib.loads(self.overlay_toml.read_text(encoding="utf-8"))
        return data.get(cat_pkg)

    def latest(self, cat_pkg: str) -> str | None:
        entry = self._entry(cat_pkg)
        if not entry:
            return None
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            cfg_path = dpath / "n.toml"
            newver = dpath / "new.json"
            cfg = {"__config__": {"newver": str(newver)}, cat_pkg: entry}
            cfg_path.write_text(tomli_w.dumps(cfg), encoding="utf-8")
            args = [self.cmd, "--file", str(cfg_path)]
            if self.keyfile:
                args += ["--keyfile", str(self.keyfile)]
            subprocess.run(args, check=True, capture_output=True, text=True)
            if newver.exists():
                data = json.loads(newver.read_text(encoding="utf-8") or "{}")
                return data.get(cat_pkg)
        return None


class PyPIProvider(VersionProvider):
    def latest(self, cat_pkg: str) -> str | None:
        pn = cat_pkg.rsplit("/", 1)[-1]
        resp = httpx.get(f"https://pypi.org/pypi/{pn}/json", timeout=30)
        resp.raise_for_status()
        return resp.json().get("info", {}).get("version")


def get_latest_version(cat_pkg: str, overlay_root: Path,
                       overlay_toml: Path | None = None,
                       keyfile: Path | None = None) -> dict:
    overlay_root = Path(overlay_root)
    overlay_toml = Path(overlay_toml) if overlay_toml else (
        overlay_root / ".github" / "workflows" / "overlay.toml")
    nvp = NvcheckerProvider(overlay_toml, keyfile=keyfile)
    ver = nvp.latest(cat_pkg)
    if ver:
        return {"cat_pkg": cat_pkg, "upstream": ver, "source": "nvchecker",
                "advisory": None}
    pypi = PyPIProvider().latest(cat_pkg)
    if pypi:
        return {"cat_pkg": cat_pkg, "upstream": pypi, "source": "pypi",
                "advisory": f"no overlay.toml entry for {cat_pkg}; "
                            f"consider adding one (see gzh nvchecker-config set)"}
    return {"cat_pkg": cat_pkg, "upstream": None, "source": "none",
            "advisory": "could not determine upstream version"}
