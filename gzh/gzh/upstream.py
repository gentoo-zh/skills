from __future__ import annotations

import json
import re
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import tomli_w


_PYPI_PROJECT_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


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

    def has_entry(self, cat_pkg: str) -> bool:
        return self._entry(cat_pkg) is not None

    def latest(self, cat_pkg: str) -> str | None:
        entry = self._entry(cat_pkg)
        if not entry:
            return None
        with tempfile.TemporaryDirectory() as d:
            dpath = Path(d)
            cfg_path = dpath / "n.toml"
            cfg = {"__config__": {}, cat_pkg: entry}
            cfg_path.write_text(tomli_w.dumps(cfg), encoding="utf-8")
            args = [self.cmd, "--file", str(cfg_path), "--logger", "json"]
            if self.keyfile:
                args += ["--keyfile", str(self.keyfile)]
            proc = subprocess.run(args, check=True, capture_output=True, text=True)
        # nvchecker --logger json emits one JSON object per line per event;
        # the result line carries name + event in {updated,new} + version.
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (ev.get("name") == cat_pkg
                    and ev.get("event") in ("updated", "new")
                    and ev.get("version") is not None):
                return ev["version"]
        return None


class PyPIProvider(VersionProvider):
    def latest(self, project: str) -> str | None:
        try:
            resp = httpx.get(f"https://pypi.org/pypi/{project}/json", timeout=30)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        return resp.json().get("info", {}).get("version")


def pypi_project(overlay_root: Path, cat_pkg: str) -> str | None:
    category, separator, package = cat_pkg.partition("/")
    if not separator or not category or not package:
        return None
    metadata = Path(overlay_root) / category / package / "metadata.xml"
    if not metadata.is_file():
        return None
    try:
        root = ET.parse(metadata).getroot()
    except ET.ParseError:
        return None
    for remote_id in root.findall(".//upstream/remote-id"):
        if remote_id.get("type") == "pypi" and remote_id.text:
            project = remote_id.text.strip()
            if _PYPI_PROJECT_RE.fullmatch(project):
                return project
    return None


def get_latest_version(cat_pkg: str, overlay_root: Path,
                       overlay_toml: Path | None = None,
                       keyfile: Path | None = None) -> dict:
    overlay_root = Path(overlay_root)
    overlay_toml = Path(overlay_toml) if overlay_toml else (
        overlay_root / ".github" / "workflows" / "overlay.toml")
    nvp = NvcheckerProvider(overlay_toml, keyfile=keyfile)
    if nvp.has_entry(cat_pkg):
        ver = nvp.latest(cat_pkg)
        if ver:
            return {"cat_pkg": cat_pkg, "upstream": ver, "source": "nvchecker",
                    "advisory": None}
        return {"cat_pkg": cat_pkg, "upstream": None, "source": "nvchecker",
                "advisory": "the configured nvchecker entry returned no recognized version"}
    project = pypi_project(overlay_root, cat_pkg)
    pypi = PyPIProvider().latest(project) if project else None
    if pypi:
        return {"cat_pkg": cat_pkg, "upstream": pypi, "source": "pypi",
                "project": project,
                "advisory": f"no overlay.toml entry for {cat_pkg}; verify the "
                            "release and decide whether tracking is appropriate"}
    return {"cat_pkg": cat_pkg, "upstream": None, "source": "none",
            "advisory": "could not determine upstream version"}
