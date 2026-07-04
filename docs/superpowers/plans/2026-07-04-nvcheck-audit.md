# `gzh nvcheck-audit` 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `gzh nvcheck-audit` 子命令——检查 overlay.toml（nvchecker 配置）与实际 ebuild 包一致性，对漏配包从 HOMEPAGE/SRC_URI 启发式推断上游、可选 `--apply` 调 `nvchecker-config set` 补配置。

**Architecture:** 业务逻辑放 `gzh/gzh/nvcheck_audit.py` 纯函数（`infer_source` 启发式、`audit` 集合差集+系统包过滤、`run_audit` 编排 dry-run/apply），click 薄壳在 `cli.py`。复用 MVP 的 `parse_ebuild`/`set_entry`/`find_overlay_root`；无新依赖。

**Tech Stack:** Python 3.11+、click、stdlib（re/tomllib/pathlib）、pytest（纯函数 + tmp_path fixture + mock set_entry）。

## Global Constraints

- **启发式推断**：从 ebuild HOMEPAGE/SRC_URI 正则匹配（github>pypi>git>unknown），不调网络/LLM。
- **dry-run 默认**：只列；`--apply` 才写 overlay.toml。
- **系统包过滤**：默认排除 `acct-group`/`acct-user`/`virtual` category；`--no-filter-system` 含。
- **overlay.toml 重写警告**：`set_entry` 重写丢注释，输出提示人工 review diff。
- **去个人化**：`find_overlay_root()`；无个人路径。
- **commit 无 AI 署名**；**TDD**；不碰 `/var/db/repos/gentoo-zh`。

## File Structure

```
gzh/
├── gzh/
│   ├── nvcheck_audit.py  # 新增：infer_source / audit / run_audit / _load_configured / _enumerate_actual
│   └── cli.py            # 修改：注册 nvcheck-audit 命令
└── tests/
    └── test_nvcheck_audit.py  # 新增：L1（纯函数 + tmp_path + mock set_entry）
```

---

### Task 1: `infer_source` 启发式纯函数

**Files:**
- Create: `gzh/gzh/nvcheck_audit.py`
- Test: `gzh/tests/test_nvcheck_audit.py`

**Interfaces:**
- Produces: `infer_source(parsed: dict, pn: str) -> tuple[str, dict | None]`（返回 `(source, entry)`；source ∈ `{"github","pypi","git","unknown"}`，unknown 时 entry 为 `None`）。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_nvcheck_audit.py`:
```python
from gzh.nvcheck_audit import infer_source


def test_infer_github_from_homepage():
    parsed = {"HOMEPAGE": "https://github.com/org/foo", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "github"
    assert entry == {"source": "github", "github": "org/foo", "use_latest_release": True}


def test_infer_github_strips_trailing_git_and_slash():
    parsed = {"HOMEPAGE": "https://github.com/org/foo.git", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert entry["github"] == "org/foo"


def test_infer_pypi_from_src_uri():
    parsed = {"HOMEPAGE": "https://example.org", "SRC_URI": "https://pypi.org/foo", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "pypi"
    assert entry == {"source": "pypi", "pypi": "foo"}


def test_infer_pypi_from_inherit():
    parsed = {"HOMEPAGE": "", "SRC_URI": "", "inherit": ["distutils-r1", "pypi"]}
    source, entry = infer_source(parsed, "foo")
    assert source == "pypi"
    assert entry["pypi"] == "foo"


def test_infer_git_from_gitlab_url():
    parsed = {"HOMEPAGE": "https://gitlab.com/org/foo", "SRC_URI": "", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "git"
    assert entry["source"] == "git"
    assert entry["use_max_tag"] is True


def test_infer_unknown_when_no_match():
    parsed = {"HOMEPAGE": "https://example.org", "SRC_URI": "https://example.org/foo.tar.gz", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "unknown"
    assert entry is None


def test_infer_github_priority_over_pypi():
    # both github homepage and pypi src_uri -> github wins
    parsed = {"HOMEPAGE": "https://github.com/org/foo", "SRC_URI": "https://pypi.org/foo", "inherit": []}
    source, entry = infer_source(parsed, "foo")
    assert source == "github"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.nvcheck_audit'`

- [ ] **Step 3: 实现**

`gzh/gzh/nvcheck_audit.py`:
```python
from __future__ import annotations

import re

_GITHUB_RE = re.compile(r'github\.com/([^/]+)/([^/)\."\'\s]+)')


def _clean_repo(repo: str) -> str:
    return repo.removesuffix(".git").rstrip("/")


def infer_source(parsed: dict, pn: str) -> tuple[str, dict | None]:
    homepage = parsed.get("HOMEPAGE", "") or ""
    src_uri = parsed.get("SRC_URI", "") or ""
    text = f"{homepage} {src_uri}"
    inherit = parsed.get("inherit", []) or []

    m = _GITHUB_RE.search(text)
    if m:
        org, repo = m.group(1), _clean_repo(m.group(2))
        return "github", {"source": "github", "github": f"{org}/{repo}",
                          "use_latest_release": True}
    if "pypi.org" in text or "files.pythonhosted.org" in text or "pypi" in inherit:
        return "pypi", {"source": "pypi", "pypi": pn}
    if "gitlab.com" in text or "codeberg.org" in text or ".git" in text:
        return "git", {"source": "git", "use_max_tag": True}
    return "unknown", None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/nvcheck_audit.py gzh/tests/test_nvcheck_audit.py
git commit -m "feat(gzh): add infer_source heuristic (github/pypi/git/unknown)"
```

---

### Task 2: `audit` 检查逻辑（stale/missing + 系统包过滤）

**Files:**
- Modify: `gzh/gzh/nvcheck_audit.py`（追加 `SYSTEM_CATEGORIES`、`_is_system`、`audit`）
- Test: `gzh/tests/test_nvcheck_audit.py`（追加测试）

**Interfaces:**
- Produces:
  - `audit(configured: set, actual: set, filter_system: bool = True) -> tuple[list[str], list[str]]`（返回 `(stale, missing)`；stale = 配置有/包无；missing = 包有/配置无，可选过滤系统包）

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_nvcheck_audit.py`:
```python
from gzh.nvcheck_audit import audit


def test_audit_stale_and_missing():
    configured = {"cat/a", "cat/b", "cat/removed"}
    actual = {"cat/a", "cat/b", "cat/new"}
    stale, missing = audit(configured, actual, filter_system=False)
    assert stale == ["cat/removed"]
    assert missing == ["cat/new"]


def test_audit_filters_system_packages_by_default():
    configured = {"cat/a"}
    actual = {"cat/a", "acct-group/x", "virtual/y", "cat/missing"}
    stale, missing = audit(configured, actual, filter_system=True)
    assert missing == ["cat/missing"]  # acct-group/virtual filtered


def test_audit_no_filter_includes_system():
    configured = {"cat/a"}
    actual = {"cat/a", "acct-group/x", "cat/missing"}
    _, missing = audit(configured, actual, filter_system=False)
    assert sorted(missing) == ["acct-group/x", "cat/missing"]


def test_audit_empty_when_consistent():
    configured = {"cat/a"}
    actual = {"cat/a"}
    stale, missing = audit(configured, actual)
    assert stale == [] and missing == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v`
Expected: FAIL — `ImportError: cannot import name 'audit'`

- [ ] **Step 3: 实现**

追加到 `gzh/gzh/nvcheck_audit.py`:
```python
SYSTEM_CATEGORIES = {"acct-group", "acct-user", "virtual"}


def _is_system(cat_pkg: str) -> bool:
    cat = cat_pkg.split("/", 1)[0]
    return cat in SYSTEM_CATEGORIES


def audit(configured: set, actual: set, filter_system: bool = True) -> tuple[list[str], list[str]]:
    stale = sorted(configured - actual)
    missing = sorted(actual - configured)
    if filter_system:
        missing = [p for p in missing if not _is_system(p)]
    return stale, missing
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v`
Expected: PASS（11 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/nvcheck_audit.py gzh/tests/test_nvcheck_audit.py
git commit -m "feat(gzh): add audit (stale/missing + system package filter)"
```

---

### Task 3: `run_audit` 编排（dry-run/apply + `_load_configured`/`_enumerate_actual`）

**Files:**
- Modify: `gzh/gzh/nvcheck_audit.py`（追加 `_load_configured`、`_enumerate_actual`、`run_audit`）
- Test: `gzh/tests/test_nvcheck_audit.py`（追加测试）

**Interfaces:**
- Consumes: `infer_source`（Task 1）、`audit`（Task 2）、`parse_ebuild`（MVP）、`set_entry`（MVP `gzh.nvchecker_config`）、`find_overlay_root`
- Produces:
  - `_load_configured(overlay_toml: Path) -> set[str]`（读 overlay.toml，返回 `cat/pkg` 集合，跳过 `__config__`）
  - `_enumerate_actual(root: Path) -> set[str]`（遍历 `root/*/*/` 含 ebuild 的包）
  - `run_audit(apply: bool = False, filter_system: bool = True, overlay_root: Path | None = None, set_entry_fn=set_entry) -> dict`（返回 `{"ok":True, "stale":[...], "missing":[{cat_pkg,source,entry,applied}], "skipped_unknown":[...]}`）

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_nvcheck_audit.py`:
```python
import tomllib
from pathlib import Path

from gzh.nvcheck_audit import _enumerate_actual, _load_configured, run_audit


def _overlay(tmp_path: Path) -> Path:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "overlay.toml").write_text(
        '__config__ = {newver = "n.json"}\n["cat/cfg"]\nsource = "github"\n'
        'github = "o/cfg"\n', encoding="utf-8")
    # actual: cat/cfg (configured), cat/gh (missing, github), cat/pyp (missing, pypi),
    # cat/unk (missing unknown), cat/cfg-rm not in actual (stale handled by set diff)
    for pkg, homepage in [("cat/cfg", "https://github.com/o/cfg"),
                          ("cat/gh", "https://github.com/o/gh"),
                          ("cat/pyp", "https://pypi.org/pyp"),
                          ("cat/unk", "https://example.org")]:
        d = tmp_path / pkg
        d.mkdir(parents=True)
        pn = pkg.split("/")[1]
        (d / f"{pn}-1.0.ebuild").write_text(
            f'EAPI=8\nHOMEPAGE="{homepage}"\nSRC_URI=""\nSLOT="0"\n', encoding="utf-8")
    return tmp_path


def test_load_configured(tmp_path):
    root = _overlay(tmp_path)
    cfg = _load_configured(root / ".github" / "workflows" / "overlay.toml")
    assert cfg == {"cat/cfg"}


def test_enumerate_actual(tmp_path):
    root = _overlay(tmp_path)
    actual = _enumerate_actual(root)
    assert actual == {"cat/cfg", "cat/gh", "cat/pyp", "cat/unk"}


def test_run_audit_dry_run(tmp_path):
    root = _overlay(tmp_path)
    set_calls = []
    res = run_audit(apply=False, overlay_root=root,
                    set_entry_fn=lambda *a, **k: set_calls.append(a))
    assert res["ok"] is True
    assert res["stale"] == []  # cat/cfg exists in both
    sources = {m["cat_pkg"]: m["source"] for m in res["missing"]}
    assert sources["cat/gh"] == "github"
    assert sources["cat/pyp"] == "pypi"
    assert res["skipped_unknown"] == ["cat/unk"]
    assert all(m["applied"] is False for m in res["missing"])
    assert set_calls == []  # dry-run: no set calls


def test_run_audit_apply_sets_entries(tmp_path):
    root = _overlay(tmp_path)
    set_calls = []
    res = run_audit(apply=True, overlay_root=root,
                    set_entry_fn=lambda toml, cat_pkg, entry: set_calls.append((cat_pkg, entry)))
    applied_pkgs = [c[0] for c in set_calls]
    assert "cat/gh" in applied_pkgs and "cat/pyp" in applied_pkgs
    assert "cat/unk" not in applied_pkgs  # unknown skipped
    assert all(m["applied"] is True for m in res["missing"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v`
Expected: FAIL — `ImportError: cannot import name '_load_configured'`

- [ ] **Step 3: 实现**

在 `gzh/gzh/nvcheck_audit.py` 顶部 import 区补：
```python
import tomllib
from pathlib import Path

from gzh.ebuild_parser import parse_ebuild
from gzh.nvchecker_config import set_entry
from gzh.repo import find_overlay_root
```
追加：
```python
def _load_configured(overlay_toml: Path) -> set[str]:
    data = tomllib.loads(Path(overlay_toml).read_text(encoding="utf-8"))
    return {k for k in data if "/" in k and k != "__config__"}


def _enumerate_actual(root: Path) -> set[str]:
    out: set[str] = set()
    for cat_d in Path(root).iterdir():
        if not cat_d.is_dir() or cat_d.name.startswith("."):
            continue
        if cat_d.name in ("metadata", "profiles"):
            continue
        for pkg_d in cat_d.iterdir():
            if pkg_d.is_dir() and any(pkg_d.glob("*.ebuild")):
                out.add(f"{cat_d.name}/{pkg_d.name}")
    return out


def run_audit(apply: bool = False, filter_system: bool = True,
              overlay_root: Path | None = None, set_entry_fn=set_entry) -> dict:
    root = Path(overlay_root) if overlay_root else find_overlay_root()
    overlay_toml = root / ".github" / "workflows" / "overlay.toml"
    configured = _load_configured(overlay_toml)
    actual = _enumerate_actual(root)
    stale, missing = audit(configured, actual, filter_system=filter_system)

    out_missing: list[dict] = []
    skipped_unknown: list[str] = []
    for cat_pkg in missing:
        cat, pn = cat_pkg.split("/", 1)
        ebs = sorted((root / cat / pn).glob(f"{pn}-*.ebuild"))
        if not ebs:
            continue
        parsed = parse_ebuild(ebs[-1])
        source, entry = infer_source(parsed, pn)
        if source == "unknown" or entry is None:
            skipped_unknown.append(cat_pkg)
            continue
        applied = False
        if apply:
            set_entry_fn(overlay_toml, cat_pkg, entry)
            applied = True
        out_missing.append({"cat_pkg": cat_pkg, "source": source,
                            "entry": entry, "applied": applied})
    return {"ok": True, "stale": stale, "missing": out_missing,
            "skipped_unknown": skipped_unknown}
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v && python -m pytest -q`
Expected: test_nvcheck_audit.py 全 PASS（15 passed）；全量无回归。

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/nvcheck_audit.py gzh/tests/test_nvcheck_audit.py
git commit -m "feat(gzh): run_audit orchestrates dry-run/apply with set_entry"
```

---

### Task 4: cli 注册 `nvcheck-audit`

**Files:**
- Modify: `gzh/gzh/cli.py`（注册 `nvcheck-audit`）
- Test: `gzh/tests/test_nvcheck_audit.py`（追加 cli 测试）

**Interfaces:**
- Consumes: `run_audit`（Task 3）
- Produces: `gzh nvcheck-audit [--apply] [--no-filter-system]`。默认 dry-run；`--apply` 调 set_entry（重写 overlay.toml，输出提示 review diff）。

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_nvcheck_audit.py`:
```python
from click.testing import CliRunner

from gzh.cli import cli


def test_nvcheck_audit_help_registered():
    result = CliRunner().invoke(cli, ["nvcheck-audit", "--help"])
    assert result.exit_code == 0
    assert "apply" in result.output.lower()


def test_nvcheck_audit_dry_run_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    # minimal overlay with one missing github pkg
    (tmp_path / "cat" / "gh").mkdir(parents=True)
    (tmp_path / "cat" / "gh" / "gh-1.0.ebuild").write_text(
        'EAPI=8\nHOMEPAGE="https://github.com/o/gh"\nSRC_URI=""\nSLOT="0"\n')
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "overlay.toml").write_text('__config__ = {newver="n.json"}\n', encoding="utf-8")
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    result = CliRunner().invoke(cli_mod.cli, ["nvcheck-audit"])
    assert result.exit_code == 0
    assert "cat/gh" in result.output
    assert '"applied": false' in result.output  # dry-run
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py::test_nvcheck_audit_help_registered -v`
Expected: FAIL — `Error: No such command 'nvcheck-audit'`

- [ ] **Step 3: 实现（注册命令）**

在 `gzh/gzh/cli.py`：
- 顶部 import 区追加：
```python
from gzh.nvcheck_audit import run_audit
```
- 在 `drop_old_cmd` 之后、`def main():` 之前追加：
```python
@cli.command("nvcheck-audit")
@click.option("--apply", is_flag=True, default=False,
              help="write inferred entries to overlay.toml (rewrites file, comments lost)")
@click.option("--no-filter-system", is_flag=True, default=False,
              help="include acct-*/virtual/* in missing check")
def nvcheck_audit_cmd(apply, no_filter_system):
    """Audit overlay.toml (nvchecker config) vs actual packages; infer upstreams."""
    res = run_audit(apply=apply, filter_system=not no_filter_system,
                    overlay_root=find_overlay_root())
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if apply and res["missing"]:
        click.echo("NOTE: overlay.toml rewritten; comments lost. Review the diff.",
                   err=True)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd gzh && python -m pytest tests/test_nvcheck_audit.py -v && python -m pytest -q`
Expected: test_nvcheck_audit.py 全 PASS（17 passed）；全量无回归。

- [ ] **Step 5: 手动验证**

Run: `gzh nvcheck-audit --help` → 应见 `--apply/--no-filter-system`。

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/cli.py gzh/tests/test_nvcheck_audit.py
git commit -m "feat(gzh): register nvcheck-audit command"
```

---

### Task 5: L3 真实端到端验证

**Files:** 无（运行验证）

- [ ] **Step 1: dry-run 真实跑（skills 内 gentoo-zh 副本）**

Run:
```bash
export GZH_OVERLAY_DIR=/home/yongxiang/work/gentoo/gentoo-zh-skills/gentoo-zh
gzh nvcheck-audit | python3 -c 'import sys,json;d=json.load(sys.stdin);print("stale:",len(d["stale"]));print("missing:",len(d["missing"]));print("skipped_unknown:",len(d["skipped_unknown"]));import collections;c=collections.Counter(m["source"] for m in d["missing"]);print("missing by source:",dict(c))'
```
Expected: stale=0；missing（过滤系统包后，应 < 227）；missing 按 source 分布（github/pypi/git）；skipped_unknown（HOMEPAGE 非上游模式）。

- [ ] **Step 2: --no-filter-system 对比**

Run: `gzh nvcheck-audit --no-filter-system | python3 -c 'import sys,json;d=json.load(sys.stdin);print("missing with system:",len(d["missing"]))'`
Expected: missing 数 > Step 1（含 acct-group/virtual）。

- [ ] **Step 3: 推断准确性抽查**

从 Step 1 输出抽 3-5 个 missing 包，人工核对推断的 source/entry 是否正确（对比 ebuild HOMEPAGE）。

- [ ] **Step 4: 若 L3 发现缺陷**

修复并补单测（缺陷 → 失败测试 → 修复 → 全量绿 → commit）。常见：启发式误推断（如 HOMEPAGE 是 github 镜像非上游）、系统包过滤不全（漏 category）。

---

## Self-Review

**1. Spec coverage（spec §10 验收）：**
- 验收#1（dry-run 列 missing+stale+unknown）：Task 5 Step 1 + Task 3 `test_run_audit_dry_run` ✅
- 验收#2（--no-filter-system 含系统包）：Task 2 `test_audit_no_filter_includes_system` + Task 5 Step 2 ✅
- 验收#3（推断 github/pypi/git/unknown）：Task 1 四类测试 ✅
- 验收#4（--apply 调 set，unknown 跳过）：Task 3 `test_run_audit_apply_sets_entries` ✅
- 验收#5（dry-run 不调 set）：Task 3 `test_run_audit_dry_run`（set_calls == []）✅
- 验收#6（L1 全绿）：Task 1-4 全 mock + tmp_path ✅

**2. Placeholder scan：** 无 TBD/TODO；每步含完整测试代码、实现代码、命令、预期。✅

**3. Type consistency：** `infer_source(parsed, pn) -> tuple[str, dict|None]`、`audit(configured, actual, filter_system=True) -> tuple[list[str], list[str]]`、`_load_configured(overlay_toml) -> set[str]`、`_enumerate_actual(root) -> set[str]`、`run_audit(apply=False, filter_system=True, overlay_root=None, set_entry_fn=set_entry) -> dict` 在 Task 1-4 间一致；返回 `{ok, stale:[cat_pkg], missing:[{cat_pkg,source,entry,applied}], skipped_unknown:[cat_pkg]}` 统一；系统包判定（`_is_system`，category ∈ SYSTEM_CATEGORIES）单点；github 提取（`_GITHUB_RE` + `_clean_repo`）单点。✅
