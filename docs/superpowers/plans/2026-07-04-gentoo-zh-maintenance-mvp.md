# Gentoo-zh 维护 Skill 套件 MVP — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `gzh` Python CLI 工具核心（11 个子命令）+ `gzh-version-bump` skill，使一个 gentoo-zh 维护者能对真实包跑通 gzh-version-bump 全流程并产出规范 commit。

**Architecture:** 方案 B——确定性动作沉淀为 Python 包 `gzh`（pytest 可测），判断交由 skill 指导 agent；执行器收尾流程内嵌 skill。每个 `gzh` 子命令的业务逻辑放在 `gzh/<mod>.py` 的纯函数里（可单测），click 包装在 `cli.py` 注册（薄壳）。

**Tech Stack:** Python 3.11+、click（CLI）、httpx（HTTP）、tomllib/tomli-w（toml）、pytest；运行依赖 Gentoo 系统的 portage/pkgdev/pkgcheck/ebuild（不在 PyPI，import 但不列为包依赖）。

## Global Constraints

- **平台**：运行于 Gentoo Linux（有 `portage`/`pkgdev`/`pkgcheck`/`ebuild`/`git`/`gh`）。
- **去个人化**：任何路径/fork/owner/maintainer 不得硬编码 `liangyongxiang`；overlay 根走 `gzh repo`（git toplevel 或 `$GZH_OVERLAY_DIR`）。
- **不写 `/var/db/repos/gentoo-zh`**（synced 副本）；只在开发副本工作。
- **`~arch` only**：lint 强制，不得出现 stable keyword。
- **commit 无 AI 署名**：`Co-Authored-By`/`🤖 Generated` 一律不写。
- **TDD**：每个子命令先写失败测试 → 实现 → 通过 → commit。
- **ebuild 写法权威**：devmanual（`docs/devmanual.md` 索引）。
- **重试上限 3 次**（运行时，skill 层；工具本身不重试）。

## File Structure

```
gzh/                              # Python 包根（独立 pip 包）
├── pyproject.toml                # setuptools, 入口 gzh=gzh.cli:main
├── gzh/
│   ├── __init__.py
│   ├── cli.py                    # click group + 各子命令薄壳注册
│   ├── repo.py                   # find_overlay_root() 定位开发副本
│   ├── ebuild_parser.py          # parse_ebuild() 轻量变量提取 + PV from filename
│   ├── lint.py                   # lint_ebuild() devmanual 规则 + ~arch
│   ├── upstream.py               # VersionProvider 接口 + NvcheckerProvider + PyPIProvider + get_latest_version()
│   ├── bump.py                   # bump_scaffold() 复制旧ebuild为新版本; diff_ebuild()
│   ├── nvchecker_config.py       # nvchecker_config_get/set 读写 overlay.toml
│   ├── manifest.py               # run_manifest() pkgdev manifest 封装
│   ├── pkgcheck.py               # run_pkgcheck() 结构化输出+严重度过滤
│   ├── buildtest.py              # run_build_test() 分级 phase 序列
│   └── commit.py                 # run_commit() pkgdev commit 封装
└── tests/
    ├── conftest.py               # fixtures: 样本 ebuild/overlay.toml/pkgcheck 输出
    ├── test_cli.py
    ├── test_repo.py
    ├── test_ebuild_parser.py
    ├── test_lint.py
    ├── test_upstream.py
    ├── test_bump.py
    ├── test_nvchecker_config.py
    ├── test_manifest.py
    ├── test_pkgcheck.py
    ├── test_buildtest.py
    └── test_commit.py
.agents/skills/gzh-version-bump/      # skill（opencode+claude 兼容路径）
├── SKILL.md
└── references/
    ├── upstream-lookup.md
    └── finish-pipeline.md
docs/devmanual.md                 # 共享权威参考索引
AGENTS.md                         # 去个人化约定 + 优先用 gzh
README.md
```

每个模块职责单一：业务逻辑=纯函数（可单测），`cli.py` 只做 click 包装与注册。

---

### Task 1: 项目脚手架与 CLI 骨架

**Files:**
- Create: `gzh/pyproject.toml`
- Create: `gzh/gzh/__init__.py`
- Create: `gzh/gzh/cli.py`
- Create: `gzh/tests/__init__.py`
- Create: `gzh/tests/conftest.py`
- Test: `gzh/tests/test_cli.py`

**Interfaces:**
- Produces: `gzh.cli.cli`（click.Group）、`gzh.cli.main()`（入口点）；后续 task 在此 group 注册命令。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_cli.py`:
```python
from click.testing import CliRunner
from gzh.cli import cli


def test_help_works():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "gzh" in result.output.lower()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh'`

- [ ] **Step 3: 写最小实现**

`gzh/pyproject.toml`:
```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "gzh"
version = "0.1.0"
description = "Deterministic tooling for gentoo-zh overlay maintenance"
requires-python = ">=3.11"
dependencies = ["click>=8.1", "httpx>=0.27", "tomli-w>=1.0"]

[project.scripts]
gzh = "gzh.cli:main"

[tool.setuptools.packages.find]
where = ["."]
include = ["gzh*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`gzh/gzh/__init__.py`:
```python
__version__ = "0.1.0"
```

`gzh/gzh/cli.py`:
```python
import click


@click.group()
@click.version_option(package_name="gzh")
def cli():
    """gzh — deterministic tooling for gentoo-zh overlay maintenance."""


def main():
    cli()


if __name__ == "__main__":
    main()
```

`gzh/tests/__init__.py`:（空文件，建立包）
`gzh/tests/conftest.py`:
```python
import sys
from pathlib import Path

# ensure repo-root `gzh` package importable without install during dev
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
```

- [ ] **Step 4: 安装可编辑包并跑测试**

Run: `cd gzh && pip install -e . && python -m pytest tests/test_cli.py -v`
Expected: PASS（1 passed）

- [ ] **Step 5: 手动验证入口**

Run: `gzh --help`
Expected: 打印帮助，含 "gzh — deterministic tooling..."。

- [ ] **Step 6: Commit**

```bash
git add gzh
git commit -m "feat(gzh): scaffold python package and click CLI skeleton"
```

---

### Task 2: `gzh repo` — 定位 overlay 开发副本

**Files:**
- Create: `gzh/gzh/repo.py`
- Modify: `gzh/gzh/cli.py`（注册 repo 命令）
- Test: `gzh/tests/test_repo.py`

**Interfaces:**
- Produces: `gzh.repo.find_overlay_root(start: Path | None = None) -> Path`——优先 `$GZH_OVERLAY_DIR`，否则 `git rev-parse --show-toplevel`；失败抛 `RuntimeError`。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_repo.py`:
```python
import subprocess
from pathlib import Path

from gzh.repo import find_overlay_root


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("GZH_OVERLAY_DIR", str(tmp_path))
    assert find_overlay_root() == tmp_path.resolve()


def test_git_toplevel_when_no_env(monkeypatch, tmp_path):
    monkeypatch.delenv("GZH_OVERLAY_DIR", raising=False)

    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{tmp_path}\n", stderr="")

    monkeypatch.setattr("gzh.repo.subprocess.run", fake_run)
    assert find_overlay_root(Path.cwd()) == tmp_path


def test_not_a_repo_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("GZH_OVERLAY_DIR", raising=False)

    def fake_run(cmd, **kw):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.setattr("gzh.repo.subprocess.run", fake_run)
    try:
        find_overlay_root(tmp_path)
    except RuntimeError:
        return
    raise AssertionError("expected RuntimeError")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_repo.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.repo'`

- [ ] **Step 3: 实现**

`gzh/gzh/repo.py`:
```python
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def find_overlay_root(start: Path | None = None) -> Path:
    env = os.environ.get("GZH_OVERLAY_DIR")
    if env:
        root = Path(env).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"GZH_OVERLAY_DIR not a directory: {root}")
        return root
    start = (start or Path.cwd()).resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"not inside a git repo: {start}") from exc
    return Path(out.stdout.strip())
```

- [ ] **Step 4: 注册 click 命令**

在 `gzh/gzh/cli.py` 的 `cli` 定义之后、`main()` 之前追加：
```python
from gzh.repo import find_overlay_root


@cli.command("repo")
def repo_cmd():
    """Print the detected overlay development checkout root."""
    click.echo(str(find_overlay_root()))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_repo.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/repo.py gzh/gzh/cli.py gzh/tests/test_repo.py
git commit -m "feat(gzh): add repo command to locate overlay checkout"
```

---

### Task 3: `gzh ebuild-parse` — 轻量解析 ebuild 变量

**Files:**
- Create: `gzh/gzh/ebuild_parser.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_ebuild_parser.py`

**Interfaces:**
- Produces: `gzh.ebuild_parser.parse_ebuild(path: Path) -> dict`——返回顶层变量（含 `PV` 取自文件名）。MVP 限制：正则提取标量顶层赋值，不展开 `${...}`、不执行 ebuild；满足 gzh-version-bump 所需（EAPI/KEYWORDS/SRC_URI/LICENSE/HOMEPAGE/DESCRIPTION/SLOT/PV）。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_ebuild_parser.py`:
```python
from pathlib import Path

from gzh.ebuild_parser import parse_ebuild

SAMPLE = '''# Copyright 1999-2026 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

EAPI=8

DESCRIPTION="Example package"
HOMEPAGE="https://example.org/${PN}"
SRC_URI="https://example.org/${P}.tar.gz"
LICENSE="MIT"
SLOT="0"
KEYWORDS="~amd64 ~arm64"
IUSE="test"
'''


def test_parse_basic(tmp_path):
    eb = tmp_path / "foo-1.2.3.ebuild"
    eb.write_text(SAMPLE)
    info = parse_ebuild(eb)
    assert info["EAPI"] == "8"
    assert info["PV"] == "1.2.3"
    assert "~amd64" in info["KEYWORDS"]
    assert info["LICENSE"] == "MIT"
    assert info["SRC_URI"] == "https://example.org/${P}.tar.gz"


def test_pv_from_revision(tmp_path):
    eb = tmp_path / "foo-1.2.3-r1.ebuild"
    eb.write_text("EAPI=8\n")
    assert parse_ebuild(eb)["PV"] == "1.2.3-r1"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_ebuild_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.ebuild_parser'`

- [ ] **Step 3: 实现**

`gzh/gzh/ebuild_parser.py`:
```python
from __future__ import annotations

import re
from pathlib import Path

_VAR_RE = re.compile(r'^([A-Z_][A-Z0-9_]*)=(.*)$', re.MULTILINE)
_QUOTED_RE = re.compile(r'^"(.*)"$')


def _pv_from_name(name: str) -> str:
    stem = name.removesuffix(".ebuild")
    m = re.match(r'^(.+)-(\d.*)$', stem)
    return m.group(2) if m else ""


def _strip(raw: str) -> str:
    raw = raw.strip()
    m = _QUOTED_RE.match(raw)
    if m:
        return m.group(1)
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1]
    return raw


def parse_ebuild(path: Path) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    text = re.sub(r"\\\n", "", text)  # join line continuations
    result: dict[str, str] = {}
    for m in _VAR_RE.finditer(text):
        result[m.group(1)] = _strip(m.group(2))
    result["PV"] = _pv_from_name(Path(path).name)
    return result
```

- [ ] **Step 4: 注册 click 命令（输出 JSON）**

在 `cli.py` 追加：
```python
import json as _json

from gzh.ebuild_parser import parse_ebuild


@cli.command("ebuild-parse")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def ebuild_parse_cmd(ebuild):
    """Print parsed ebuild variables as JSON."""
    click.echo(_json.dumps(parse_ebuild(ebuild), indent=2, ensure_ascii=False))
```
（`Path` 需 `from pathlib import Path`；`click` 已导入。下同，后续 task 不再重复 import 说明。）

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_ebuild_parser.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/ebuild_parser.py gzh/gzh/cli.py gzh/tests/test_ebuild_parser.py
git commit -m "feat(gzh): add ebuild-parse command with lightweight variable parser"
```

---

### Task 4: `gzh lint` — devmanual 规则 + ~arch 检查

**Files:**
- Create: `gzh/gzh/lint.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_lint.py`

**Interfaces:**
- Consumes: `gzh.ebuild_parser.parse_ebuild` 的返回 dict。
- Produces: `gzh.lint.lint_ebuild(parsed: dict) -> list[dict]`，每项 `{"severity": "error"|"warning", "rule": str, "msg": str}`。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_lint.py`:
```python
from gzh.lint import lint_ebuild


def _good():
    return {
        "EAPI": "8", "KEYWORDS": "~amd64 ~arm64", "LICENSE": "MIT",
        "SRC_URI": "https://x/${P}.tar.gz", "HOMEPAGE": "https://x",
        "DESCRIPTION": "x", "SLOT": "0", "PV": "1.0",
    }


def test_clean_ebuild_no_issues():
    assert lint_ebuild(_good()) == []


def test_stable_keyword_is_error():
    bad = _good()
    bad["KEYWORDS"] = "amd64 ~arm64"
    issues = lint_ebuild(bad)
    assert any(i["rule"] == "stable-keyword" and i["severity"] == "error" for i in issues)


def test_unsupported_eapi_is_error():
    bad = _good()
    bad["EAPI"] = "5"
    issues = lint_ebuild(bad)
    assert any(i["rule"] == "eapi-unsupported" for i in issues)


def test_missing_license_is_error():
    bad = _good()
    bad["LICENSE"] = ""
    assert any(i["rule"] == "missing-license" for i in issues)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_lint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.lint'`

- [ ] **Step 3: 实现**

`gzh/gzh/lint.py`:
```python
from __future__ import annotations

SUPPORTED_EAPI = {"7", "8"}
REQUIRED_VARS = ("DESCRIPTION", "HOMEPAGE", "LICENSE", "SRC_URI", "SLOT")


def lint_ebuild(parsed: dict) -> list[dict]:
    issues: list[dict] = []
    eapi = str(parsed.get("EAPI", "")).strip()
    if not eapi:
        issues.append({"severity": "error", "rule": "eapi-missing",
                       "msg": "EAPI not set"})
    elif eapi not in SUPPORTED_EAPI:
        issues.append({"severity": "error", "rule": "eapi-unsupported",
                       "msg": f"EAPI={eapi} unsupported (expect {sorted(SUPPORTED_EAPI)})"})
    keywords = str(parsed.get("KEYWORDS", "")).split()
    stable = [k for k in keywords if k and not k.startswith("~") and k not in ("*", "-*", "-**")]
    if stable:
        issues.append({"severity": "error", "rule": "stable-keyword",
                       "msg": f"gentoo-zh allows ~arch only; found stable: {stable}"})
    for var in REQUIRED_VARS:
        if not str(parsed.get(var, "")).strip():
            issues.append({"severity": "error", "rule": f"missing-{var.lower()}",
                           "msg": f"{var} not set"})
    return issues
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.ebuild_parser import parse_ebuild
from gzh.lint import lint_ebuild


@cli.command("lint")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def lint_cmd(ebuild):
    """Lint one ebuild against devmanual rules + gentoo-zh ~arch policy."""
    issues = lint_ebuild(parse_ebuild(ebuild))
    click.echo(_json.dumps(issues, indent=2, ensure_ascii=False))
    if any(i["severity"] == "error" for i in issues):
        raise SystemExit(1)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_lint.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/lint.py gzh/gzh/cli.py gzh/tests/test_lint.py
git commit -m "feat(gzh): add lint command for devmanual + ~arch rules"
```

---

### Task 5: `gzh upstream-version` — VersionProvider（nvchecker 优先 + 回退）

**Files:**
- Create: `gzh/gzh/upstream.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_upstream.py`

**Interfaces:**
- Produces:
  - `gzh.upstream.VersionProvider`（基类，方法 `latest(cat_pkg: str) -> str | None`）
  - `gzh.upstream.NvcheckerProvider(overlay_toml: Path, keyfile: Path | None = None)`
  - `gzh.upstream.PyPIProvider()`（httpx 回退）
  - `gzh.upstream.get_latest_version(cat_pkg: str, overlay_root: Path) -> dict`，形如 `{"cat_pkg": ..., "upstream": "...", "source": "nvchecker|pypi|none", "advisory": str | None}`

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_upstream.py`:
```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_upstream.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.upstream'`

- [ ] **Step 3: 实现**

`gzh/gzh/upstream.py`:
```python
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
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.upstream import get_latest_version


@cli.command("upstream-version")
@click.argument("cat_pkg")
def upstream_version_cmd(cat_pkg):
    """Look up the latest upstream version for category/package."""
    res = get_latest_version(cat_pkg, find_overlay_root())
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_upstream.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/upstream.py gzh/gzh/cli.py gzh/tests/test_upstream.py
git commit -m "feat(gzh): add upstream-version with nvchecker provider + pypi fallback"
```

---

### Task 6: `gzh bump-scaffold` + `gzh diff-ebuild`

**Files:**
- Create: `gzh/gzh/bump.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_bump.py`

**Interfaces:**
- Produces:
  - `gzh.bump.highest_ebuild(pkg_dir: Path, pn: str) -> Path | None`
  - `gzh.bump.bump_scaffold(pkg_dir: Path, pn: str, new_pv: str) -> Path`（复制最高旧 ebuild 为 `<pn>-<new_pv>.ebuild`，返回新路径）
  - `gzh.bump.diff_ebuild(old: Path, new: Path) -> str`（unified diff）

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_bump.py`:
```python
import difflib
from pathlib import Path

from gzh.bump import bump_scaffold, diff_ebuild, highest_ebuild


def _pkgdir(tmp_path: Path) -> Path:
    d = tmp_path / "dev-python" / "foo"
    d.mkdir(parents=True)
    (d / "foo-1.0.0.ebuild").write_text('EAPI=8\nDESCRIPTION="x"\nPV_OLD="1.0.0"\n')
    (d / "foo-1.1.0.ebuild").write_text('EAPI=8\nDESCRIPTION="x"\nPV_OLD="1.1.0"\n')
    return d


def test_highest_ebuild(tmp_path):
    d = _pkgdir(tmp_path)
    assert highest_ebuild(d, "foo").name == "foo-1.1.0.ebuild"


def test_bump_scaffold_copies_highest(tmp_path):
    d = _pkgdir(tmp_path)
    new = bump_scaffold(d, "foo", "1.2.0")
    assert new.name == "foo-1.2.0.ebuild"
    assert new.exists()
    # content copied from highest (1.1.0), filename changed
    assert 'PV_OLD="1.1.0"' in new.read_text()


def test_diff_ebuild(tmp_path):
    d = _pkgdir(tmp_path)
    old = d / "foo-1.0.0.ebuild"
    new = d / "foo-1.1.0.ebuild"
    diff = diff_ebuild(old, new)
    assert "PV_OLD" in diff
    assert diff.startswith("---")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_bump.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.bump'`

- [ ] **Step 3: 实现**

`gzh/gzh/bump.py`:
```python
from __future__ import annotations

import difflib
import shutil
from pathlib import Path


def _ebuilds(pkg_dir: Path, pn: str) -> list[Path]:
    return sorted(pkg_dir.glob(f"{pn}-*.ebuild"))


def highest_ebuild(pkg_dir: Path, pn: str) -> Path | None:
    ebs = _ebuilds(pkg_dir, pn)
    return ebs[-1] if ebs else None


def bump_scaffold(pkg_dir: Path, pn: str, new_pv: str) -> Path:
    src = highest_ebuild(pkg_dir, pn)
    if src is None:
        raise FileNotFoundError(f"no existing ebuild for {pn} in {pkg_dir}")
    dst = pkg_dir / f"{pn}-{new_pv}.ebuild"
    if dst.exists():
        raise FileExistsError(f"target already exists: {dst}")
    shutil.copy2(src, dst)
    return dst


def diff_ebuild(old: Path, new: Path) -> str:
    return "".join(difflib.unified_diff(
        old.read_text().splitlines(keepends=True),
        new.read_text().splitlines(keepends=True),
        fromfile=str(old), tofile=str(new)))
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.bump import bump_scaffold, diff_ebuild, highest_ebuild


@cli.command("bump-scaffold")
@click.argument("cat_pkg")
@click.argument("new_pv")
def bump_scaffold_cmd(cat_pkg, new_pv):
    """Copy the highest existing ebuild to <pn>-<new_pv>.ebuild."""
    root = find_overlay_root()
    category, pn = cat_pkg.split("/", 1)
    pkg_dir = root / category / pn
    dst = bump_scaffold(pkg_dir, pn, new_pv)
    click.echo(str(dst))


@cli.command("diff-ebuild")
@click.argument("old", type=click.Path(exists=True, path_type=Path))
@click.argument("new", type=click.Path(exists=True, path_type=Path))
def diff_ebuild_cmd(old, new):
    """Print a unified diff between two ebuilds."""
    click.echo(diff_ebuild(old, new), nl=False)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_bump.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/bump.py gzh/gzh/cli.py gzh/tests/test_bump.py
git commit -m "feat(gzh): add bump-scaffold and diff-ebuild commands"
```

---

### Task 7: `gzh nvchecker-config` — overlay.toml 读写

**Files:**
- Create: `gzh/gzh/nvchecker_config.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_nvchecker_config.py`

**Interfaces:**
- Produces:
  - `gzh.nvchecker_config.get_entry(overlay_toml: Path, cat_pkg: str) -> dict | None`
  - `gzh.nvchecker_config.set_entry(overlay_toml: Path, cat_pkg: str, entry: dict) -> None`（用 tomli_w 重写整文件；**注意：会丢失注释**，CLI 输出需提示人工 review）

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_nvchecker_config.py`:
```python
from pathlib import Path

from gzh.nvchecker_config import get_entry, set_entry


def test_get_entry(tmp_path):
    t = tmp_path / "overlay.toml"
    t.write_text('["dev-python/foo"]\nsource = "github"\ngithub = "x/foo"\n')
    assert get_entry(t, "dev-python/foo") == {"source": "github", "github": "x/foo"}
    assert get_entry(t, "dev-python/missing") is None


def test_set_entry_roundtrip(tmp_path):
    t = tmp_path / "overlay.toml"
    t.write_text('[__config__]\nnewver = "n.json"\n')
    set_entry(t, "dev-python/bar", {"source": "pypi", "pypi": "bar"})
    assert get_entry(t, "dev-python/bar") == {"source": "pypi", "pypi": "bar"}
    # config section preserved
    import tomllib
    assert tomllib.loads(t.read_text())["__config__"]["newver"] == "n.json"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_nvchecker_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.nvchecker_config'`

- [ ] **Step 3: 实现**

`gzh/gzh/nvchecker_config.py`:
```python
from __future__ import annotations

import tomllib
import tomli_w
from pathlib import Path


def _load(path: Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def get_entry(overlay_toml: Path, cat_pkg: str) -> dict | None:
    return _load(overlay_toml).get(cat_pkg)


def set_entry(overlay_toml: Path, cat_pkg: str, entry: dict) -> None:
    data = _load(overlay_toml)
    data[cat_pkg] = entry
    Path(overlay_toml).write_text(tomli_w.dumps(data), encoding="utf-8")
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.nvchecker_config import get_entry, set_entry


@cli.command("nvchecker-config")
@click.argument("cat_pkg")
@click.argument("action", type=click.Choice(["get", "set"]))
@click.option("--source", help="nvchecker source key, e.g. github/pypi/git")
@click.option("--json", "json_entry", help="full entry as JSON (for set)")
def nvchecker_config_cmd(cat_pkg, action, source, json_entry):
    """Read or write a package's nvchecker entry in overlay.toml."""
    root = find_overlay_root()
    overlay_toml = root / ".github" / "workflows" / "overlay.toml"
    if action == "get":
        click.echo(_json.dumps(get_entry(overlay_toml, cat_pkg), indent=2,
                               ensure_ascii=False))
    else:
        if not json_entry:
            raise click.UsageError("--json is required for set")
        set_entry(overlay_toml, cat_pkg, _json.loads(json_entry))
        click.echo("NOTE: overlay.toml rewritten; comments lost. Review the diff.")
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_nvchecker_config.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/nvchecker_config.py gzh/gzh/cli.py gzh/tests/test_nvchecker_config.py
git commit -m "feat(gzh): add nvchecker-config get/set for overlay.toml"
```

---

### Task 8: `gzh manifest` — pkgdev manifest 封装

**Files:**
- Create: `gzh/gzh/manifest.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_manifest.py`

**Interfaces:**
- Produces: `gzh.manifest.run_manifest(ebuild: Path, cwd: Path | None = None, runner=subprocess.run) -> dict`——调 `pkgdev manifest`，返回 `{"ok": bool, "returncode": int, "stdout": str, "stderr": str}`。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_manifest.py`:
```python
import subprocess
from pathlib import Path

from gzh.manifest import run_manifest


def test_manifest_success(monkeypatch, tmp_path):
    captured = {}

    def fake_run(args, **kw):
        captured["args"] = args
        captured["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    res = run_manifest(eb, cwd=tmp_path, runner=fake_run)
    assert res["ok"] is True
    assert captured["args"][:2] == ["pkgdev", "manifest"]
    assert "--force" in captured["args"]
    assert captured["cwd"] == tmp_path


def test_manifest_failure(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="fetch failed")
    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    res = run_manifest(eb, cwd=tmp_path, runner=fake_run)
    assert res["ok"] is False
    assert "fetch failed" in res["stderr"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.manifest'`

- [ ] **Step 3: 实现**

`gzh/gzh/manifest.py`:
```python
from __future__ import annotations

import subprocess
from pathlib import Path


def run_manifest(ebuild: Path, cwd: Path | None = None,
                 runner=subprocess.run) -> dict:
    args = ["pkgdev", "manifest", "--force", str(ebuild)]
    proc = runner(args, cwd=cwd, capture_output=True, text=True)
    return {"ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.manifest import run_manifest


@cli.command("manifest")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
def manifest_cmd(ebuild):
    """Regenerate the Manifest for an ebuild via pkgdev."""
    pkg_dir = Path(ebuild).parent
    res = run_manifest(ebuild, cwd=pkg_dir)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_manifest.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/manifest.py gzh/gzh/cli.py gzh/tests/test_manifest.py
git commit -m "feat(gzh): add manifest command wrapping pkgdev manifest"
```

---

### Task 9: `gzh pkgcheck` — 结构化 QA 扫描

**Files:**
- Create: `gzh/gzh/pkgcheck.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_pkgcheck.py`

**Interfaces:**
- Produces: `gzh.pkgcheck.run_pkgcheck(path: Path, min_severity: str = "warning", runner=subprocess.run) -> dict`——调 `pkgcheck scan --format json`，解析结果，按严重度过滤；返回 `{"ok": bool, "results": [ {...} ], "raw_returncode": int}`。严重度序：error > warning > info > style。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_pkgcheck.py`:
```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_pkgcheck.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.pkgcheck'`

- [ ] **Step 3: 实现**

`gzh/gzh/pkgcheck.py`:
```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path

SEVERITY_ORDER = {"error": 40, "warning": 30, "info": 20, "style": 10}


def _flatten(parsed: list) -> list[dict]:
    out = []
    for block in parsed or []:
        for r in block.get("results", []):
            out.append(r)
    return out


def run_pkgcheck(path: Path, min_severity: str = "warning",
                 runner=subprocess.run) -> dict:
    args = ["pkgcheck", "scan", "--format", "json", str(path)]
    proc = runner(args, cwd=str(path) if path.is_dir() else None,
                  capture_output=True, text=True)
    try:
        parsed = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        parsed = []
    threshold = SEVERITY_ORDER.get(min_severity, 0)
    results = [r for r in _flatten(parsed)
               if SEVERITY_ORDER.get(r.get("severity", "info"), 0) >= threshold]
    has_error = any(r.get("severity") == "error" for r in results)
    return {"ok": not has_error, "results": results,
            "raw_returncode": proc.returncode}
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.pkgcheck import run_pkgcheck


@cli.command("pkgcheck")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--min-severity", default="warning",
              type=click.Choice(["error", "warning", "info", "style"]))
def pkgcheck_cmd(path, min_severity):
    """Run pkgcheck scan and print structured results filtered by severity."""
    res = run_pkgcheck(path, min_severity=min_severity)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_pkgcheck.py -v`
Expected: PASS（3 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/pkgcheck.py gzh/gzh/cli.py gzh/tests/test_pkgcheck.py
git commit -m "feat(gzh): add pkgcheck command with structured, severity-filtered output"
```

---

### Task 10: `gzh build-test` — 分级编译验证

**Files:**
- Create: `gzh/gzh/buildtest.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_buildtest.py`

**Interfaces:**
- Produces: `gzh.buildtest.run_build_test(ebuild: Path, level: str = "quick", runner=subprocess.run) -> dict`。级别 phase 序（对齐 devmanual）：
  - `none` → 不跑（返回 `{"ok": True, "skipped": True, "reason": "level=none"}`）
  - `quick` → `clean unpack prepare configure`
  - `full` → `clean unpack prepare configure compile install`
  - 失败时定位失败 phase：`{"ok": bool, "level": str, "failed_phase": str | None, "log_path": str | None, "stdout": str, "stderr": str, "returncode": int}`

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_buildtest.py`:
```python
import subprocess
from pathlib import Path

from gzh.buildtest import run_build_test


def _eb(tmp_path):
    eb = tmp_path / "foo-1.0.0.ebuild"
    eb.write_text("EAPI=8\n")
    return eb


def test_none_level_skips(tmp_path):
    res = run_build_test(_eb(tmp_path), level="none")
    assert res["ok"] is True
    assert res["skipped"] is True


def test_quick_runs_expected_phases(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_build_test(_eb(tmp_path), level="quick", runner=fake_run)
    assert res["ok"] is True
    phases = seen["args"][seen["args"].index("clean"):]
    assert phases == ["clean", "unpack", "prepare", "configure"]


def test_full_includes_compile_install(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    run_build_test(_eb(tmp_path), level="full", runner=fake_run)
    a = seen["args"]
    assert "compile" in a and "install" in a


def test_failure_locates_phase(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        # fails at 'prepare'
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="patch fails")

    res = run_build_test(_eb(tmp_path), level="quick", runner=fake_run)
    assert res["ok"] is False
    assert res["failed_phase"] == "prepare"
    assert "patch fails" in res["stderr"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_buildtest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.buildtest'`

- [ ] **Step 3: 实现**

`gzh/gzh/buildtest.py`:
```python
from __future__ import annotations

import subprocess
from pathlib import Path

PHASES = {
    "quick": ["clean", "unpack", "prepare", "configure"],
    "full": ["clean", "unpack", "prepare", "configure", "compile", "install"],
}


def run_build_test(ebuild: Path, level: str = "quick",
                   runner=subprocess.run) -> dict:
    if level == "none":
        return {"ok": True, "level": level, "skipped": True,
                "reason": "level=none", "failed_phase": None,
                "log_path": None, "stdout": "", "stderr": "",
                "returncode": 0}
    phases = PHASES[level]
    failed_phase = None
    stdout_parts, stderr_parts = [], []
    rc = 0
    for phase in phases:
        args = ["ebuild", str(ebuild), phase]
        proc = runner(args, capture_output=True, text=True)
        stdout_parts.append(proc.stdout)
        stderr_parts.append(proc.stderr)
        rc = proc.returncode
        if proc.returncode != 0:
            failed_phase = phase
            break
    log_path = None
    # Portage writes temp logs; best-effort pointer (not guaranteed to exist)
    return {"ok": failed_phase is None, "level": level,
            "failed_phase": failed_phase, "log_path": log_path,
            "stdout": "".join(stdout_parts), "stderr": "".join(stderr_parts),
            "returncode": rc, "skipped": False}
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.buildtest import run_build_test


@cli.command("build-test")
@click.argument("ebuild", type=click.Path(exists=True, path_type=Path))
@click.option("--level", default="quick",
              type=click.Choice(["none", "quick", "full"]))
def build_test_cmd(ebuild, level):
    """Run a staged ebuild build test (none/quick/full)."""
    res = run_build_test(ebuild, level=level)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_buildtest.py -v`
Expected: PASS（4 passed）

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/buildtest.py gzh/gzh/cli.py gzh/tests/test_buildtest.py
git commit -m "feat(gzh): add build-test command with quick/full/none levels"
```

---

### Task 11: `gzh commit` — pkgdev commit 封装

**Files:**
- Create: `gzh/gzh/commit.py`
- Modify: `gzh/gzh/cli.py`
- Test: `gzh/tests/test_commit.py`

**Interfaces:**
- Produces: `gzh.commit.run_commit(paths: list[Path], cwd: Path, message: str | None = None, runner=subprocess.run) -> dict`——调 `pkgdev commit`（可选 `--message`），返回 `{"ok": bool, "returncode": int, "stdout": str, "stderr": str}`。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_commit.py`:
```python
import subprocess
from pathlib import Path

from gzh.commit import run_commit


def test_commit_with_explicit_message(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        seen["cwd"] = kw.get("cwd")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    res = run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
                     message="dev-python/foo: add 1.0.0", runner=fake_run)
    assert res["ok"] is True
    assert seen["args"][:2] == ["pkgdev", "commit"]
    assert "--message" in seen["args"]
    assert "dev-python/foo: add 1.0.0" in seen["args"]
    assert seen["cwd"] == tmp_path


def test_commit_without_message(monkeypatch, tmp_path):
    def fake_run(args, **kw):
        assert "--message" not in args
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
    res = run_commit([tmp_path / "foo-1.0.0.ebuild"], cwd=tmp_path,
                     runner=fake_run)
    assert res["ok"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_commit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.commit'`

- [ ] **Step 3: 实现**

`gzh/gzh/commit.py`:
```python
from __future__ import annotations

import subprocess
from pathlib import Path


def run_commit(paths: list[Path], cwd: Path,
               message: str | None = None,
               runner=subprocess.run) -> dict:
    args = ["pkgdev", "commit"]
    if message:
        args += ["--message", message]
    args += [str(p) for p in paths]
    proc = runner(args, cwd=str(cwd), capture_output=True, text=True)
    return {"ok": proc.returncode == 0, "returncode": proc.returncode,
            "stdout": proc.stdout, "stderr": proc.stderr}
```

- [ ] **Step 4: 注册 click 命令**

在 `cli.py` 追加：
```python
from gzh.commit import run_commit


@cli.command("commit")
@click.argument("paths", nargs=-1, type=click.Path(path_type=Path))
@click.option("--message", "-m", default=None)
def commit_cmd(paths, message):
    """Commit via pkgdev (no AI attribution; gentoo-zh style)."""
    if not paths:
        raise click.UsageError("at least one path required")
    res = run_commit(list(paths), cwd=find_overlay_root(), message=message)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if not res["ok"]:
        raise SystemExit(1)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_commit.py -v`
Expected: PASS（2 passed）

- [ ] **Step 6: 跑全量测试确认无回归**

Run: `cd gzh && python -m pytest -q`
Expected: 全部 PASS

- [ ] **Step 7: Commit**

```bash
git add gzh/gzh/commit.py gzh/gzh/cli.py gzh/tests/test_commit.py
git commit -m "feat(gzh): add commit command wrapping pkgdev commit"
```

---

### Task 12: 共享文档（devmanual 索引 + AGENTS.md + README）

**Files:**
- Create: `docs/devmanual.md`
- Create: `AGENTS.md`
- Create: `README.md`

**Interfaces:** 无（纯文档）。这些文档是 skill 与维护者的权威依据。

- [ ] **Step 1: 写 `docs/devmanual.md`**

内容为 devmanual 章节索引（带链接），权威参考。完整内容见 spec §17，要点：
```markdown
# devmanual — 权威参考索引

所有 ebuild 写法以 Gentoo 官方 devmanual 为唯一标准。skill 引用本文件，不在 skill 文档里抄规范。

- 根: https://devmanual.gentoo.org/ebuild-writing/index.html
- file-format: https://devmanual.gentoo.org/ebuild-writing/file-format/index.html
- EAPI: https://devmanual.gentoo.org/ebuild-writing/eapi/index.html
- variables: https://devmanual.gentoo.org/ebuild-writing/variables/index.html
- functions (phase 顺序 pkg_pretend→...→pkg_postinst, default_*): https://devmanual.gentoo.org/ebuild-writing/functions/index.html
- use-conditional-code: https://devmanual.gentoo.org/ebuild-writing/use-conditional-code/index.html
- using-eclasses: https://devmanual.gentoo.org/ebuild-writing/using-eclasses/index.html
- error-handling: https://devmanual.gentoo.org/ebuild-writing/error-handling/index.html
- common-mistakes: https://devmanual.gentoo.org/ebuild-writing/common-mistakes/index.html
- misc-files (metadata.xml/patches): https://devmanual.gentoo.org/ebuild-writing/misc-files/index.html

gentoo-zh 附加铁律：KEYWORDS 仅 ~arch，无 stable。
```

- [ ] **Step 2: 写 `AGENTS.md`**

```markdown
# Agent 约定

## 工具优先级
gentoo-zh 维护**优先使用 `gzh` 工具集**（确定性、可单测），不要现想 bash 命令拼凑。
安装: `pip install -e ./gzh`。

## 去个人化（硬原则）
- overlay 根用 `gzh repo`（git toplevel 或 `$GZH_OVERLAY_DIR`），不硬编码任何个人路径。
- PR head 动态取 `gh api user`，不写死 fork owner。
- 不写 `/var/db/repos/gentoo-zh`（synced 副本）；只在开发副本工作。

## gentoo-zh 规则
- `~arch` only，无 stable keyword。
- 一包一分支一 commit 一 PR，分支从 `origin/master` 切（`category-package-${VERSION}`）。
- commit 无 AI 署名（`Co-Authored-By`/`🤖 Generated` 一律不写）。
- 提交前必过 `gzh lint` + `gzh manifest` + `gzh pkgcheck`（🔴硬门）。
- ebuild 写法以 `docs/devmanual.md` 为权威。
- 重试上限 3 次：同一错误重复 2 次即停，报告并询问。
```

- [ ] **Step 3: 写 `README.md`**

```markdown
# Gentoo-zh skills

gentoo-zh overlay 维护的 opencode/claude skill 套件 + `gzh` Python 工具。

## 安装
\`\`\`bash
pip install -e ./gzh          # 安装 gzh CLI
# symlink skill 到发现路径（opencode/claude 均兼容 .agents/skills/）：
ln -s "$PWD/.agents/skills/gzh-version-bump" ~/.agents/skills/gzh-version-bump
export GZH_OVERLAY_DIR=/path/to/gentoo-zh-dev-checkout
\`\`\`

## 文档
- 设计: `docs/superpowers/specs/2026-07-04-gentoo-zh-maintenance-skill-design.md`
- 实现: `docs/superpowers/plans/2026-07-04-gentoo-zh-maintenance-mvp.md`
- devmanual 索引: `docs/devmanual.md`
- 约定: `AGENTS.md`
```

- [ ] **Step 4: 校验文档链接可达**

Run: `curl -sI https://devmanual.gentoo.org/ebuild-writing/index.html | head -1`
Expected: `HTTP/2 200`

- [ ] **Step 5: Commit**

```bash
git add docs/devmanual.md AGENTS.md README.md
git commit -m "docs: add devmanual index, AGENTS conventions, README"
```

---

### Task 13: `gzh-version-bump` skill

**Files:**
- Create: `.agents/skills/gzh-version-bump/SKILL.md`
- Create: `.agents/skills/gzh-version-bump/references/upstream-lookup.md`
- Create: `.agents/skills/gzh-version-bump/references/finish-pipeline.md`

**Interfaces:** skill 通过 opencode `skill` 工具按需加载；引用 `docs/devmanual.md` 与各 `gzh` 命令。

- [ ] **Step 1: 写 `SKILL.md`（frontmatter 遵守 opencode 规则）**

```markdown
---
name: gzh-version-bump
description: "Bump an existing gentoo-zh package to a new upstream version. Trigger on requests like 'bump dev-python/foo', '升级 wechat', 'update to 1.2.3', or package atoms needing a new version. Covers upstream lookup, scaffolding, dep/patch assessment, and the manifest→pkgcheck→build-test→commit finish pipeline. Only for gentoo-zh overlay (~arch only). Skip new-package creation and main gentoo tree."
---

# gzh-version-bump — 为现有 gentoo-zh 包升版本

仅负责执行器的**阶段 A（特化改动）**，完成后按 [finish-pipeline.md](references/finish-pipeline.md) 走收尾。

## 前置约束（见 AGENTS.md）
- `~arch` only、一包一分支一 PR、commit 无 AI 署名。
- ebuild 写法权威：`docs/devmanual.md`。
- 用 `gzh` 工具，不现想 bash。

## 阶段 A 步骤

1. **A1 确认版本**：`gzh upstream-version <cat/pkg>` 查上游最新；`gzh ebuild-parse <最高旧 ebuild>` 读当前 PV。**不过滤预发布**（上游发布即可 bump）。若上游 ≤ 当前，停止并报告。
2. **A2 版本号规范化**（查 devmanual 版本规则）：tag `v1.2.3`→PV `1.2.3`；`rc/beta/pre`→`_rc/_beta/_pre`。
3. **A3 脚手架**：`git fetch origin && git checkout -b <cat>-<pn>-<new_pv> origin/master`，然后 `gzh bump-scaffold <cat/pkg> <new_pv>`。
4. **A4 SRC_URI**：检查新版本归档 URL 是否仍有效（`-bin` 包重点）。fetch 错误由收尾阶段 `gzh manifest` 暴露。
5. **A5 依赖评估**（源码包重点）：对照上游新版本依赖变化（requirements/CHANGES/meson.build），最小改动 `DEPEND/RDEPEND`。
6. **A6 patch 评估**（★最易出错）：读 `files/` 下旧 patch，对照新版本源码判断是否仍适用；失败则重新生成或删除。改 patch 后会触发收尾 `build-test` 的 patch test。
7. **A7 旧版本处理**：**默认只 add 不 drop**。drop 是独立能力（定时任务/手动），不在本 skill。
8. **A8 nvchecker 配置**：若 `gzh upstream-version` 返回 `advisory`（无 overlay.toml 条目），用 `gzh nvchecker-config set <cat/pkg> --json '...'` 补上，并提示人工 review（注释会丢失）。

**区分包类型**：`-bin` 包重在 A4（SRC_URI）；源码包重在 A5/A6（依赖/patch）。

## 收尾
阶段 A 完成后，**无条件**进入 [finish-pipeline.md](references/finish-pipeline.md)（校验→Manifest→QA→build-test→diff→commit）。重试上限 3 次。

## 排除
- 新建包（用 create，未实现）。
- 修复编译失败（若 build-test 失败，转 fix-build-failure，未实现）。
```

- [ ] **Step 2: 写 `references/finish-pipeline.md`**

```markdown
# 收尾流程（执行器，所有硬门必须过）

阶段 A 产出"改好的新 ebuild"后，依次：

1. **校验（🔴硬门）**：`gzh lint <new_ebuild>`——devmanual 规则 + ~arch。Error 即停。
2. **Manifest（🔴硬门）**：`gzh manifest <new_ebuild>`——fetch distfiles + checksums。fetch 失败即停（检查 SRC_URI）。
3. **QA（🔴硬门 Error）**：`gzh pkgcheck <pkg_dir> --min-severity error`——Error 必须清零；Warning 记录不阻断。
4. **编译验证（🟡软门）**：`gzh build-test <new_ebuild> --level quick`（源码包默认 quick；`-bin` 包用 `full`；巨大/GUI 包用 `none` 但**必须**在交付报告注明跳过原因）。失败→提示转 fix-build-failure。
5. **变更摘要**：`gzh diff-ebuild <old_ebuild> <new_ebuild>`，总结改动与理由。
6. **提交**：`gzh commit <new_ebuild> <Manifest> [-m "category/package: add <PV>"]`（默认不 drop）。commit 无 AI 署名。
7. **交付**：默认停在此（本地分支 + commit）。开 PR 需显式：`gh pr create --repo gentoo-zh/overlay --base master --head $(gh api user --jq .login):<branch>`（head 动态取，不写死）。

**重试上限 3 次**：任一硬门/编译同一错误重复 2 次即停，报告失败步骤+错误+每次尝试，询问继续/跳过/放弃。
```

- [ ] **Step 3: 写 `references/upstream-lookup.md`**

```markdown
# 上游版本查询策略

`gzh upstream-version <cat/pkg>` 内部策略（见 spec §6）：

1. **NvcheckerProvider（默认）**：读 overlay 的 `.github/workflows/overlay.toml`，提取该包 source 配置，调 `nvchecker` 取最新版。source 类型：`github`（use_latest_release/use_latest_tag）、`git`、`pypi`、`apt`、`regex` 等（见 nvchecker 文档）。
2. **PyPIProvider（回退）**：overlay.toml 无该包条目时，查 `https://pypi.org/pypi/<pn>/json`。
3. 无结果：返回 `source=none` + advisory。

返回结构：`{"cat_pkg", "upstream", "source", "advisory"}`。`advisory` 非空时，gzh-version-bump 的 A8 步骤应补 overlay.toml 条目（`gzh nvchecker-config set`）。

**新包无配置**：上游类型判断后用 `gzh nvchecker-config set <cat/pkg> --json '{"source":"github","github":"org/repo","use_latest_release":true}'`，注意 set 会重写 overlay.toml（丢注释），务必人工 review diff。
```

- [ ] **Step 4: 校验 skill name 合规（opencode 规则）**

Run: `python -c "import re,sys; n='gzh-version-bump'; assert re.match(r'^[a-z0-9]+(-[a-z0-9]+)*$', n) and n==__import__('pathlib').Path('.agents/skills/gzh-version-bump').name; print('ok')"`
Expected: `ok`

- [ ] **Step 5: 手动触发冒烟（如已装 opencode/claude）**

可选：在 gentoo-zh 开发副本里对一个小 `-bin` 包验证 skill 能被发现并触发 `gzh` 链。若环境不具备，跳过（L3 端到端冒烟在实现后补）。

- [ ] **Step 6: Commit**

```bash
git add .agents/skills/gzh-version-bump
git commit -m "feat(skill): add gzh-version-bump skill with finish pipeline and upstream lookup"
```

---

## Self-Review

**1. Spec coverage（spec §15 MVP 验收）：**
- §5 `gzh` MVP 子命令（11 个）：repo(T2)/ebuild-parse(T3)/lint(T4)/upstream-version(T5)/bump-scaffold+diff-ebuild(T6)/nvchecker-config(T7)/manifest(T8)/pkgcheck(T9)/build-test(T10)/commit(T11) ✅
- `gzh-version-bump` skill + references：T13 ✅
- `docs/devmanual.md`：T12 ✅
- `AGENTS.md`（去个人化约定 + 优先用 gzh）：T12 ✅
- L1 pytest：每个 task 均含 ✅
- 验收#1（真实包跑通全流程）：由 T13 SKILL.md 编排 11 个 gzh 命令实现；端到端冒烟在实现后执行 ✅
- 验收#3（去个人化，任何维护者可用）：repo 走 env/git(T2)、PR head 动态(T13 finish-pipeline)、AGENTS.md 约定(T12)；代码无 `liangyongxiang` ✅
- 缺口：无。验收#2（L1 覆盖）由全量 `pytest -q`(T11 Step6) 保证 ✅

**2. Placeholder scan：** 无 TBD/TODO；每步含实际代码、命令、预期输出。✅

**3. Type consistency：** 各 task 产出的函数签名（`find_overlay_root`、`parse_ebuild`、`lint_ebuild`、`get_latest_version`、`bump_scaffold`/`diff_ebuild`、`get_entry`/`set_entry`、`run_manifest`、`run_pkgcheck`、`run_build_test`、`run_commit`）在 cli.py 注册处与 skill 文档引用处一致；严重度序 `SEVERITY_ORDER`、`PHASES` 常量在测试与实现间一致。✅

（spec §13 L2 触发 eval 已明确降级，不进 MVP，故无对应 task，符合范围。）
