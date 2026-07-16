# `gzh bump-issues` 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `gzh bump-issues` 子命令——通过 `gh api graphql` 单次批量查询读取 `Gentoo-zh/gentoo-zh` 上 `label=nvchecker` 的 bump reminder issue（含 comments），解析成结构化 JSON 队列，纯只读，衔接现有 gzh-version-bump skill。

**Architecture:** 业务逻辑放 `gzh/gzh/bump_issues.py` 的纯函数（解析/映射/过滤/GraphQL 构造），`gh` 调用与退出码编排放 `run_bump_issues(runner=)`（runner 注入可单测），click 薄壳在 `cli.py` 注册。与 MVP 其他命令同一模式（`run_manifest`/`run_pkgcheck` 风格）。

**Tech Stack:** Python 3.11+、click、stdlib（json/re/subprocess）、gh CLI（已认证，运行时依赖）；pytest（mock runner，不真连 GitHub）。

## Global Constraints

- **平台**：运行于已 `gh auth login` 的环境（gh 是运行时依赖）。
- **去个人化**：仓库默认 `gentoo-zh/overlay`（组织固定名）+ `--repo` 覆盖；无个人路径/owner 硬编码。
- **纯只读**：仅 `gh api graphql` 查询；不修改 issue、不 push、不 PR、不碰 `/var/db/repos`。
- **TDD**：每步先写失败测试 → 实现 → 通过 → commit；gh 输出全 mock。
- **GitHub issue 状态**：只有 OPEN/CLOSED（无 MERGED）；`--state all` 不传 `states` 参数。
- **commit 无 AI 署名**（`Co-Authored-By`/`🤖 Generated` 一律不写）。

## File Structure

```
gzh/
├── gzh/
│   ├── bump_issues.py    # 新增：parse_title/parse_body/graphql_to_queue/apply_filters/build_query/run_bump_issues
│   └── cli.py            # 修改：注册 bump-issues 命令
└── tests/
    └── test_bump_issues.py  # 新增：L1 全 mock
```

`bump_issues.py` 单文件含全部逻辑（解析/映射/过滤/构造/编排），职责内聚；文件不大（~120 行），无需拆分。

---

### Task 1: 标题与 body 解析纯函数

**Files:**
- Create: `gzh/gzh/bump_issues.py`
- Test: `gzh/tests/test_bump_issues.py`

**Interfaces:**
- Produces: `parse_title(title: str) -> tuple[str, str] | None`（返回 `(cat_pkg, target_version)`，不匹配返回 `None`）；`parse_body(body: str) -> dict`（返回 `{"oldver": str|None, "maintainer": str|None}`）。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_bump_issues.py`:
```python
from gzh.bump_issues import parse_body, parse_title


def test_parse_title_typical():
    assert parse_title("[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40") == \
        ("media-fonts/sarasa-gothic", "1.0.40")


def test_parse_title_bin_and_version_suffix():
    assert parse_title("[nvchecker] net-proxy/naiveproxy-bin can be bump to 150.0.7871.63_p1") == \
        ("net-proxy/naiveproxy-bin", "150.0.7871.63_p1")


def test_parse_title_unmatched_returns_none():
    assert parse_title("some other title") is None
    assert parse_title("[nvchecker] not the bump pattern") is None


def test_parse_body_fields():
    assert parse_body("oldver: 1.0.39\nCC: @Linerre") == \
        {"oldver": "1.0.39", "maintainer": "Linerre"}


def test_parse_body_missing_fields():
    assert parse_body("nothing useful here") == \
        {"oldver": None, "maintainer": None}


def test_parse_body_cc_without_at_sign():
    assert parse_body("CC: someone")["maintainer"] == "someone"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.bump_issues'`

- [ ] **Step 3: 实现**

`gzh/gzh/bump_issues.py`:
```python
from __future__ import annotations

import json
import re
import subprocess

_TITLE_RE = re.compile(r'^\[nvchecker\]\s+(\S+)\s+can be bump to\s+(\S+)$')
_OLDVER_RE = re.compile(r'oldver:\s*(\S+)', re.IGNORECASE)
_CC_RE = re.compile(r'CC:\s*@?(\S+)', re.IGNORECASE)


def parse_title(title: str) -> tuple[str, str] | None:
    m = _TITLE_RE.match((title or "").strip())
    return (m.group(1), m.group(2)) if m else None


def parse_body(body: str) -> dict:
    body = body or ""
    m_old = _OLDVER_RE.search(body)
    m_cc = _CC_RE.search(body)
    return {"oldver": m_old.group(1) if m_old else None,
            "maintainer": m_cc.group(1) if m_cc else None}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v`
Expected: PASS（6 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/bump_issues.py gzh/tests/test_bump_issues.py
git commit -m "feat(gzh): add nvchecker issue title/body parsers"
```

---

### Task 2: GraphQL 响应映射 + 过滤

**Files:**
- Modify: `gzh/gzh/bump_issues.py`（追加 `graphql_to_queue`、`apply_filters`）
- Test: `gzh/tests/test_bump_issues.py`（追加测试）

**Interfaces:**
- Consumes: `parse_title`, `parse_body`（Task 1）
- Produces: `graphql_to_queue(nodes: list, with_comments: bool = True) -> tuple[list[dict], int]`（返回 `(queue, skipped_count)`，标题不匹配的 node 计入 skipped）；`apply_filters(queue: list, maintainer: str | None = None, pkg: str | None = None) -> list[dict]`。

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_bump_issues.py`:
```python
from gzh.bump_issues import apply_filters, graphql_to_queue

NODES = [
    {"number": 10581,
     "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
     "body": "oldver: 1.0.39\nCC: @Linerre", "state": "OPEN",
     "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
     "comments": {"nodes": [
         {"author": {"login": "microcai"}, "body": "hi",
          "createdAt": "2026-07-01T00:00:00Z"}]}},
    {"number": 999, "title": "random nvchecker note", "body": "",
     "state": "OPEN", "url": "u", "comments": {"nodes": []}},
]


def test_graphql_to_queue_skips_unmatched():
    queue, skipped = graphql_to_queue(NODES)
    assert skipped == 1
    assert len(queue) == 1
    item = queue[0]
    assert item["issue"] == 10581
    assert item["cat_pkg"] == "media-fonts/sarasa-gothic"
    assert item["target_version"] == "1.0.40"
    assert item["oldver"] == "1.0.39"
    assert item["maintainer"] == "Linerre"
    assert item["state"] == "open"
    assert item["url"].endswith("/issues/10581")
    assert item["comments_truncated"] is False
    assert item["comments"][0]["author"] == "microcai"


def test_graphql_to_queue_no_comments_option():
    queue, _ = graphql_to_queue(NODES, with_comments=False)
    assert queue[0]["comments"] == []
    assert queue[0]["comments_truncated"] is False


def test_graphql_to_queue_truncates_over_50_comments():
    many = [{"author": {"login": "x"}, "body": "y", "createdAt": "z"}] * 51
    nodes = [{"number": 1, "title": "[nvchecker] a/b can be bump to 1",
              "body": "", "state": "OPEN", "url": "u", "comments": {"nodes": many}}]
    queue, _ = graphql_to_queue(nodes)
    assert len(queue[0]["comments"]) == 50
    assert queue[0]["comments_truncated"] is True


def test_apply_filters_by_maintainer_and_pkg():
    queue, _ = graphql_to_queue(NODES)
    assert len(apply_filters(queue, maintainer="Linerre")) == 1
    assert apply_filters(queue, maintainer="nobody") == []
    assert len(apply_filters(queue, pkg="media-fonts/sarasa-gothic")) == 1
    assert apply_filters(queue, pkg="x/y") == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v`
Expected: FAIL — `ImportError: cannot import name 'graphql_to_queue'`

- [ ] **Step 3: 实现**

追加到 `gzh/gzh/bump_issues.py`（在 `parse_body` 之后）:
```python
def graphql_to_queue(nodes: list, with_comments: bool = True) -> tuple[list[dict], int]:
    queue: list[dict] = []
    skipped = 0
    for n in nodes or []:
        parsed = parse_title(n.get("title", ""))
        if parsed is None:
            skipped += 1
            continue
        cat_pkg, target = parsed
        body = parse_body(n.get("body", "") or "")
        item = {
            "issue": n.get("number"),
            "cat_pkg": cat_pkg,
            "target_version": target,
            "oldver": body["oldver"],
            "maintainer": body["maintainer"],
            "title": n.get("title", ""),
            "url": n.get("url"),
            "state": (n.get("state") or "").lower() or None,
        }
        if with_comments:
            cnodes = ((n.get("comments") or {}).get("nodes")) or []
            item["comments"] = [
                {"author": (c.get("author") or {}).get("login"),
                 "body": c.get("body"),
                 "created_at": c.get("createdAt")}
                for c in cnodes[:50]
            ]
            item["comments_truncated"] = len(cnodes) > 50
        else:
            item["comments"] = []
            item["comments_truncated"] = False
        queue.append(item)
    return queue, skipped


def apply_filters(queue: list, maintainer: str | None = None,
                  pkg: str | None = None) -> list[dict]:
    out = queue
    if maintainer:
        out = [x for x in out if x.get("maintainer") == maintainer]
    if pkg:
        out = [x for x in out if x.get("cat_pkg") == pkg]
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/bump_issues.py gzh/tests/test_bump_issues.py
git commit -m "feat(gzh): map graphql issue nodes to bump queue with filters"
```

---

### Task 3: GraphQL 查询构造 + gh 编排（run_bump_issues）

**Files:**
- Modify: `gzh/gzh/bump_issues.py`（追加 `build_query`、`_check_gh_auth`、`run_bump_issues`）
- Test: `gzh/tests/test_bump_issues.py`（追加测试）

**Interfaces:**
- Consumes: `graphql_to_queue`, `apply_filters`（Task 2）
- Produces:
  - `build_query(owner: str, name: str, state: str | None, limit: int, with_comments: bool) -> str`（`state` 为 `"OPEN"`/`"CLOSED"`/`None`，`None` 表示不传 `states`）
  - `run_bump_issues(repo: str = "gentoo-zh/overlay", state: str = "open", maintainer: str | None = None, pkg: str | None = None, with_comments: bool = True, limit: int = 200, runner=subprocess.run) -> dict`（返回 `{"ok": bool, "results": [...], "skipped": int, "exit_code": int, ...}`；失败时含 `error`/`stderr`）

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_bump_issues.py`:
```python
import json
import subprocess

from gzh.bump_issues import build_query, run_bump_issues


def test_build_query_open_with_comments():
    q = build_query("Gentoo-zh", "gentoo-zh", "OPEN", 200, True)
    assert 'owner:"Gentoo-zh"' in q
    assert 'name:"gentoo-zh"' in q
    assert 'labels:["nvchecker"]' in q
    assert "states:[OPEN]" in q
    assert "first:200" in q
    assert "comments(first:50)" in q


def test_build_query_all_state_no_comments():
    q = build_query("Gentoo-zh", "gentoo-zh", None, 50, False)
    assert "states:" not in q
    assert "comments" not in q


def _resp():
    return {"data": {"repository": {"issues": {"nodes": [
        {"number": 10581,
         "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
         "body": "oldver: 1.0.39\nCC: @Linerre", "state": "OPEN",
         "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
         "comments": {"nodes": []}},
    ]}}}}


def test_run_bump_issues_success():
    def fake_run(args, **kw):
        if args[:2] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(_resp()), "")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is True
    assert res["results"][0]["cat_pkg"] == "media-fonts/sarasa-gothic"
    assert res["skipped"] == 0
    assert res["exit_code"] == 0


def test_run_bump_issues_not_authenticated():
    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "not logged in")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 2


def test_run_bump_issues_gh_failure_after_auth():
    def fake_run(args, **kw):
        if args[:2] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "rate limited")
    res = run_bump_issues(runner=fake_run)
    assert res["ok"] is False
    assert res["exit_code"] == 1
    assert "rate limited" in res["stderr"]


def test_run_bump_issues_filters_pass_through():
    def fake_run(args, **kw):
        if args[:2] == ["gh", "auth", "status"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, json.dumps(_resp()), "")
    res = run_bump_issues(maintainer="nobody", runner=fake_run)
    assert res["ok"] is True
    assert res["results"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_query'`

- [ ] **Step 3: 实现**

追加到 `gzh/gzh/bump_issues.py`:
```python
_STATE_MAP = {"open": "OPEN", "closed": "CLOSED"}


def build_query(owner: str, name: str, state: str | None,
                limit: int, with_comments: bool) -> str:
    args = ['labels:["nvchecker"]', f"first:{limit}"]
    if state:
        args.append(f"states:[{state}]")
    comments_block = " comments(first:50){nodes{author{login} body createdAt}}" if with_comments else ""
    return (
        "query {\n"
        f'  repository(owner:"{owner}",name:"{name}") {{\n'
        f"    issues({', '.join(args)}) {{\n"
        f"      nodes {{ number title body state url author {{login}}{comments_block} }}\n"
        "    }\n"
        "  }\n"
        "}\n"
    )


def _check_gh_auth(runner) -> bool:
    return runner(["gh", "auth", "status"], capture_output=True, text=True).returncode == 0


def run_bump_issues(repo: str = "gentoo-zh/overlay", state: str = "open",
                    maintainer: str | None = None, pkg: str | None = None,
                    with_comments: bool = True, limit: int = 200,
                    runner=subprocess.run) -> dict:
    if not _check_gh_auth(runner):
        return {"ok": False, "exit_code": 2,
                "error": "gh not authenticated; run `gh auth login` first"}
    owner, _, name = repo.partition("/")
    if not name:
        return {"ok": False, "exit_code": 1,
                "error": f"invalid --repo: {repo!r} (expect owner/name)"}
    gstate = _STATE_MAP.get(state)  # None for "all"
    query = build_query(owner, name, gstate, limit, with_comments)
    proc = runner(["gh", "api", "graphql", "-f", f"query={query}"],
                  capture_output=True, text=True)
    if proc.returncode != 0:
        return {"ok": False, "exit_code": 1,
                "error": "gh graphql call failed", "stderr": proc.stderr}
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "exit_code": 1,
                "error": "invalid JSON from gh", "stdout": proc.stdout}
    if data.get("errors"):
        return {"ok": False, "exit_code": 1, "error": str(data["errors"])}
    nodes = (((data.get("data") or {}).get("repository") or {})
             .get("issues") or {}).get("nodes") or []
    queue, skipped = graphql_to_queue(nodes, with_comments=with_comments)
    queue = apply_filters(queue, maintainer=maintainer, pkg=pkg)
    return {"ok": True, "results": queue, "skipped": skipped, "exit_code": 0}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/bump_issues.py gzh/tests/test_bump_issues.py
git commit -m "feat(gzh): run_bump_issues orchestrates gh graphql with auth/error handling"
```

---

### Task 4: cli 注册 + 全量回归

**Files:**
- Modify: `gzh/gzh/cli.py`（注册 `bump-issues` 命令）
- Test: `gzh/tests/test_bump_issues.py`（cli 冒烟）

**Interfaces:**
- Consumes: `run_bump_issues`（Task 3）
- Produces: `gzh bump-issues` 子命令（输出 JSON，退出码反映 `exit_code`）。

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_bump_issues.py`:
```python
from click.testing import CliRunner

from gzh.cli import cli


def test_bump_issues_help_registered():
    result = CliRunner().invoke(cli, ["bump-issues", "--help"])
    assert result.exit_code == 0
    assert "nvchecker" in result.output.lower() or "bump" in result.output.lower()


def test_bump_issues_not_authenticated_exits_2(monkeypatch):
    import gzh.cli as cli_mod

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "not logged in")
    monkeypatch.setattr("gzh.bump_issues.subprocess.run", fake_run)
    result = CliRunner().invoke(cli_mod.cli, ["bump-issues"])
    assert result.exit_code == 2
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py::test_bump_issues_help_registered -v`
Expected: FAIL — `Error: No such command 'bump-issues'`

- [ ] **Step 3: 实现（注册命令）**

在 `gzh/gzh/cli.py`：
- 顶部 import 区追加（与现有 `from gzh.bump import ...` 等同级）：
```python
from gzh.bump_issues import run_bump_issues
```
- 在 `commit_cmd` 定义之后、`def main():` 之前追加：
```python
@cli.command("bump-issues")
@click.option("--repo", default="gentoo-zh/overlay", show_default=True)
@click.option("--state", default="open", show_default=True,
              type=click.Choice(["open", "all", "closed"]))
@click.option("--maintainer", default=None, help="filter by issue body 'CC: @<name>'")
@click.option("--pkg", default=None, help="filter by cat/pkg")
@click.option("--comments/--no-comments", default=True, show_default=True)
@click.option("--limit", default=200, show_default=True, type=int)
def bump_issues_cmd(repo, state, maintainer, pkg, comments, limit):
    """List nvchecker bump-reminder issues as a JSON queue (read-only)."""
    res = run_bump_issues(repo=repo, state=state, maintainer=maintainer, pkg=pkg,
                          with_comments=comments, limit=limit)
    exit_code = res.pop("exit_code", 0)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    if exit_code:
        raise SystemExit(exit_code)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd gzh && python -m pytest tests/test_bump_issues.py -v && python -m pytest -q`
Expected: test_bump_issues.py 全 PASS（17 passed）；全量无回归（之前 35 + 新增 = 应全绿）。

- [ ] **Step 5: 手动验证入口**

Run: `gzh bump-issues --help`
Expected: 打印帮助，含 `--repo/--state/--maintainer/--pkg/--comments/--limit`。

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/cli.py gzh/tests/test_bump_issues.py
git commit -m "feat(gzh): register bump-issues command in cli"
```

---

### Task 5: L3 真实端到端验证

**Files:** 无（运行验证）

**Interfaces:** 对真实 `Gentoo-zh/gentoo-zh` 跑 `gzh bump-issues`，确认队列非空且结构正确。

- [ ] **Step 1: 默认全量跑**

Run: `gzh bump-issues | python -c "import sys,json;d=json.load(sys.stdin);print('ok=',d['ok'],'count=',len(d['results']),'skipped=',d['skipped']);print([ (r['issue'],r['cat_pkg'],r['target_version']) for r in d['results'][:3]])"`
Expected: `ok=True`，count 约 20+，前几项含 `media-fonts/sarasa-gothic → 1.0.40`（issue #10581）。skipped ≥ 0。

- [ ] **Step 2: 过滤验证**

Run: `gzh bump-issues --pkg media-fonts/sarasa-gothic`
Expected: 单条结果，`cat_pkg=media-fonts/sarasa-gothic`、`target_version=1.0.40`、`comments` 为数组。

Run: `gzh bump-issues --no-comments --pkg media-fonts/sarasa-gothic`
Expected: `comments: []`、`comments_truncated: false`。

- [ ] **Step 3: 退出码验证（认证态）**

Run: `gzh bump-issues --pkg media-fonts/sarasa-gothic; echo "exit=$?"`
Expected: `exit=0`。

- [ ] **Step 4: 若 L3 发现缺陷**

修复并补单测（同 MVP L3 流程：缺陷 → 失败测试 → 修复 → 全量绿 → commit）。若无缺陷，Task 5 无需 commit（纯验证）。

---

## Self-Review

**1. Spec coverage（spec §11 验收）：**
- 验收#1（默认跑、含 sarasa 1.0.40）：Task 5 Step 1 ✅
- 验收#2（`--maintainer` 过滤）：Task 2 `test_apply_filters_by_maintainer_and_pkg` + Task 5 可顺手验 ✅
- 验收#3（`--no-comments`）：Task 2 `test_graphql_to_queue_no_comments_option` + Task 5 Step 2 ✅
- 验收#4（`--pkg` 单包）：Task 2 过滤测试 + Task 5 Step 2 ✅
- 验收#5（L1 全绿、退出码）：Task 1-4 全 mock 测试 + cli 退出码测试 ✅
- 验收#6（去个人化、`--repo` 可覆盖）：`build_query`/`run_bump_issues` 接收任意 `owner/name`，Task 3 `test_build_query_*` 用任意 owner 验证；默认值 `gentoo-zh/overlay` 非个人路径 ✅
- §7 错误处理（未认证=2 / gh 失败=1 / 标题不匹配跳过 / comments 截断）：Task 3 `test_run_bump_issues_not_authenticated`/`_gh_failure_after_auth`、Task 2 `_skips_unmatched`/`_truncates_over_50_comments` ✅
- §8 测试列表逐条覆盖：Task 1-4 测试名与 spec §8 表对应 ✅

**2. Placeholder scan：** 无 TBD/TODO；每步含完整测试代码、实现代码、命令、预期输出。✅

**3. Type consistency：** `parse_title -> tuple|None`、`parse_body -> {oldver,maintainer}`、`graphql_to_queue -> (queue, skipped)`、`apply_filters(queue,...) -> list`、`build_query(...)->str`、`run_bump_issues(...)->dict`（含 `exit_code`）在 Task 1-4 间一致；`run_bump_issues` 内 `res.pop("exit_code")` 与 cli 退出码衔接一致；`_STATE_MAP`（open→OPEN/closed→CLOSED/all→None）与 spec §5 修正后的两态一致。✅
