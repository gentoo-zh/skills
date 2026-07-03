# `gzh-bump-from-issues` 编排闭环实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `gzh-bump-from-issues` skill（agent 编排）+ `gzh triage`（跳过记录 JSONL 读写）+ `gzh notify telegram`（env 配置回报），把 `gzh bump-issues`（发现）与 `gzh-version-bump` skill（执行）编排成半自动闭环。

**Architecture:** 三件套——编排/评估是 skill 文档（agent 判断）；跳过记录读写、telegram 发送是确定性数据/IO，沉淀为 gzh 命令的纯函数（`runner`/`http` 注入可单测），click 薄壳在 `cli.py` 注册。复用 MVP 的 11 个 gzh 子命令 + gzh-version-bump 的 finish-pipeline，不改执行层。

**Tech Stack:** Python 3.11+、click、httpx、stdlib（json/pathlib/datetime/os）；gh（运行时，bump-issues 已用）；pytest（mock 文件/httpx，不真连 Telegram/GitHub）。

## Global Constraints

- **去个人化**：telegram token/chat 走 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env；PR head 用 `$(gh api user --jq .login)`；无 `liangyongxiang` 硬编码。
- **安全边界**：bump 到**本地 commit 为止**（skill 不自动 push/PR）；不碰 `/var/db/repos`；每包独立分支。
- **commit 无 AI 署名**（`Co-Authored-By`/`🤖 Generated` 一律不写）。
- **TDD**：每步先写失败测试 → 确认失败 → 实现 → 通过 → commit；文件 IO 全用 `tmp_path`，httpx 全 mock。
- **triage log**：JSONL，每行一个 JSON 对象，`#` 开头与空行跳过；文件不存在视为空。
- **notify 失败非致命**：缺 token / API 错误返回 `{"ok": false, ...}`，cli **不**非 0 退出（通知是辅助，不阻塞主流程）。

## File Structure

```
gzh/
├── gzh/
│   ├── triage.py     # 新增：list_skipped / skip_issue（JSONL 读写）
│   ├── notify.py     # 新增：send_telegram（httpx 封装）
│   └── cli.py        # 修改：注册 triage group（list/skip）+ notify group（telegram）
└── tests/
    ├── test_triage.py   # 新增
    └── test_notify.py   # 新增
.agents/skills/gzh-bump-from-issues/
└── SKILL.md             # 新增：4 阶段编排指导（纯文档，无代码）
```

每个 gzh 模块职责单一：业务逻辑=纯函数（可单测），`cli.py` 只做 click 包装。

---

### Task 1: `gzh triage` —— JSONL 跳过记录纯函数

**Files:**
- Create: `gzh/gzh/triage.py`
- Test: `gzh/tests/test_triage.py`

**Interfaces:**
- Produces:
  - `list_skipped(log_path: Path, pkg: str | None = None) -> list[dict]`（文件不存在返回 `[]`；逐行解析 JSON，跳过空行/`#` 注释/坏行；`pkg` 过滤 `cat_pkg`）
  - `skip_issue(log_path: Path, issue: int, cat_pkg: str, target_version: str, reason: str) -> dict`（`mkdir parents`，追加一行 JSON 含 `skipped_at` ISO 时间，返回该 dict）

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_triage.py`:
```python
from gzh.triage import list_skipped, skip_issue


def test_list_empty_when_no_file(tmp_path):
    assert list_skipped(tmp_path / "skip-log.jsonl") == []


def test_skip_appends_and_list_roundtrip(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    rec = skip_issue(log, 10588, "net-proxy/v2rayA", "2.4.6", "crash")
    assert rec["issue"] == 10588
    assert rec["cat_pkg"] == "net-proxy/v2rayA"
    assert rec["target_version"] == "2.4.6"
    assert rec["reason"] == "crash"
    assert rec["skipped_at"]  # non-empty ISO timestamp
    listed = list_skipped(log)
    assert len(listed) == 1
    assert listed[0]["issue"] == 10588


def test_list_filter_by_pkg(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    skip_issue(log, 1, "a/b", "1", "r1")
    skip_issue(log, 2, "c/d", "2", "r2")
    assert len(list_skipped(log, pkg="a/b")) == 1
    assert list_skipped(log, pkg="a/b")[0]["issue"] == 1


def test_list_ignores_blank_comment_bad_lines(tmp_path):
    log = tmp_path / "skip-log.jsonl"
    log.write_text("# header comment\n{bad json\n\n", encoding="utf-8")
    skip_issue(log, 3, "e/f", "3", "r3")
    listed = list_skipped(log)
    assert len(listed) == 1
    assert listed[0]["issue"] == 3


def test_skip_creates_parent_dir(tmp_path):
    log = tmp_path / "sub" / "nested" / "skip-log.jsonl"
    skip_issue(log, 9, "x/y", "1", "r")
    assert log.exists()
    assert len(list_skipped(log)) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_triage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.triage'`

- [ ] **Step 3: 实现**

`gzh/gzh/triage.py`:
```python
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def list_skipped(log_path: Path, pkg: str | None = None) -> list[dict]:
    log_path = Path(log_path)
    if not log_path.exists():
        return []
    out: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if pkg and rec.get("cat_pkg") != pkg:
            continue
        out.append(rec)
    return out


def skip_issue(log_path: Path, issue: int, cat_pkg: str,
               target_version: str, reason: str) -> dict:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rec = {"issue": issue, "cat_pkg": cat_pkg, "target_version": target_version,
           "reason": reason,
           "skipped_at": datetime.now().isoformat(timespec="seconds")}
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_triage.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/triage.py gzh/tests/test_triage.py
git commit -m "feat(gzh): add triage skip-log JSONL read/write pure functions"
```

---

### Task 2: `gzh triage` cli 注册（list/skip）

**Files:**
- Modify: `gzh/gzh/cli.py`（注册 `triage` group）
- Test: `gzh/tests/test_triage.py`（追加 cli 测试）

**Interfaces:**
- Consumes: `list_skipped`, `skip_issue`（Task 1）；`find_overlay_root`
- Produces: `gzh triage list [--pkg <cat/pkg>]`、`gzh triage skip <issue> --cat-pkg <p> --target-version <v> --reason <text>`。log 路径固定为 `find_overlay_root()/triage/skip-log.jsonl`。

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_triage.py`:
```python
from click.testing import CliRunner

from gzh.cli import cli


def test_triage_list_help_registered():
    result = CliRunner().invoke(cli, ["triage", "list", "--help"])
    assert result.exit_code == 0


def test_triage_skip_and_list_via_cli(tmp_path, monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.setattr("gzh.cli.find_overlay_root", lambda: tmp_path)
    r1 = CliRunner().invoke(cli_mod.cli,
                            ["triage", "skip", "100",
                             "--cat-pkg", "a/b", "--target-version", "1.0",
                             "--reason", "testing"])
    assert r1.exit_code == 0
    import json as _json
    assert _json.loads(r1.output)["issue"] == 100
    r2 = CliRunner().invoke(cli_mod.cli, ["triage", "list"])
    assert r2.exit_code == 0
    listed = _json.loads(r2.output)
    assert len(listed) == 1
    assert listed[0]["cat_pkg"] == "a/b"
    # file landed under <root>/triage/skip-log.jsonl
    assert (tmp_path / "triage" / "skip-log.jsonl").exists()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_triage.py::test_triage_list_help_registered -v`
Expected: FAIL — `Error: No such command 'triage'`

- [ ] **Step 3: 实现（注册命令）**

在 `gzh/gzh/cli.py`：
- 顶部 import 区追加（与其他 `from gzh.<mod> import ...` 同级）：
```python
from gzh.triage import list_skipped, skip_issue
```
- 在 `bump_issues_cmd` 定义之后、`def main():` 之前追加：
```python
@cli.group("triage")
def triage_group():
    """Read/write the bump skip log (triage/skip-log.jsonl)."""


@triage_group.command("list")
@click.option("--pkg", default=None, help="filter by cat/pkg")
def triage_list_cmd(pkg):
    """List skipped issues from the skip log."""
    root = find_overlay_root()
    records = list_skipped(root / "triage" / "skip-log.jsonl", pkg=pkg)
    click.echo(_json.dumps(records, indent=2, ensure_ascii=False))


@triage_group.command("skip")
@click.argument("issue", type=int)
@click.option("--cat-pkg", required=True)
@click.option("--target-version", required=True)
@click.option("--reason", required=True)
def triage_skip_cmd(issue, cat_pkg, target_version, reason):
    """Append a skip record to the skip log."""
    root = find_overlay_root()
    rec = skip_issue(root / "triage" / "skip-log.jsonl", issue, cat_pkg,
                     target_version, reason)
    click.echo(_json.dumps(rec, indent=2, ensure_ascii=False))
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd gzh && python -m pytest tests/test_triage.py -v && python -m pytest -q`
Expected: test_triage.py 全 PASS（7 passed）；全量无回归。

- [ ] **Step 5: 手动验证**

Run: `gzh triage --help` → 应见 `list`/`skip` 子命令。

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/cli.py gzh/tests/test_triage.py
git commit -m "feat(gzh): register triage list/skip commands"
```

---

### Task 3: `gzh notify` —— telegram 发送纯函数

**Files:**
- Create: `gzh/gzh/notify.py`
- Test: `gzh/tests/test_notify.py`

**Interfaces:**
- Produces: `send_telegram(message: str, chat_id: str | None = None, token: str | None = None, client=httpx) -> dict`。
  - token 取参或 `TELEGRAM_BOT_TOKEN` env；chat 取参或 `TELEGRAM_CHAT_ID` env。
  - 缺 token/缺 chat → `{"ok": false, "error": "...not set; skipped"}`，**不抛**。
  - 发送：`client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={chat_id, text, parse_mode="Markdown"}, timeout=30)`，`raise_for_status` 捕获 → 失败 dict；成功 `{"ok": true, "status": <code>}`。

- [ ] **Step 1: 写失败测试**

`gzh/tests/test_notify.py`:
```python
import httpx

from gzh.notify import send_telegram


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)


def test_missing_token_skips(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    res = send_telegram("hi")
    assert res["ok"] is False
    assert "TELEGRAM_BOT_TOKEN" in res["error"]


def test_missing_chat_skips(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    res = send_telegram("hi")
    assert res["ok"] is False
    assert "TELEGRAM_CHAT_ID" in res["error"]


def test_send_success(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp(200)

    monkeypatch.setattr("gzh.notify.httpx.post", fake_post)
    res = send_telegram("hello")
    assert res["ok"] is True
    assert res["status"] == 200
    assert "/bot" in captured["url"] and "sendMessage" in captured["url"]
    assert captured["json"]["text"] == "hello"
    assert captured["json"]["parse_mode"] == "Markdown"


def test_send_api_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr("gzh.notify.httpx.post", lambda *a, **k: _Resp(400))
    res = send_telegram("hi")
    assert res["ok"] is False
    assert res["status"] == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'gzh.notify'`

- [ ] **Step 3: 实现**

`gzh/gzh/notify.py`:
```python
from __future__ import annotations

import os

import httpx


def send_telegram(message: str, chat_id: str | None = None,
                  token: str | None = None, client=httpx) -> dict:
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set; skipped"}
    if not chat:
        return {"ok": False, "error": "TELEGRAM_CHAT_ID not set; skipped"}
    resp = client.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": message, "parse_mode": "Markdown"},
        timeout=30,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc),
                "status": getattr(resp, "status_code", None)}
    return {"ok": True, "status": resp.status_code}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd gzh && python -m pytest tests/test_notify.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Commit**

```bash
git add gzh/gzh/notify.py gzh/tests/test_notify.py
git commit -m "feat(gzh): add send_telegram with env-based token/chat and graceful skip"
```

---

### Task 4: `gzh notify telegram` cli 注册

**Files:**
- Modify: `gzh/gzh/cli.py`（注册 `notify` group）
- Test: `gzh/tests/test_notify.py`（追加 cli 测试）

**Interfaces:**
- Consumes: `send_telegram`（Task 3）
- Produces: `gzh notify telegram --message <text> [--chat <chat_id>]`。退出码恒 0（失败非致命，见 Global Constraints）。

- [ ] **Step 1: 写失败测试**

追加到 `gzh/tests/test_notify.py`:
```python
from click.testing import CliRunner

from gzh.cli import cli


def test_notify_telegram_help_registered():
    result = CliRunner().invoke(cli, ["notify", "telegram", "--help"])
    assert result.exit_code == 0


def test_notify_telegram_missing_token_exits_zero(monkeypatch):
    import gzh.cli as cli_mod
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    result = CliRunner().invoke(cli_mod.cli,
                                ["notify", "telegram", "--message", "hi"])
    assert result.exit_code == 0  # non-fatal
    assert '"ok": false' in result.output
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd gzh && python -m pytest tests/test_notify.py::test_notify_telegram_help_registered -v`
Expected: FAIL — `Error: No such command 'notify'`

- [ ] **Step 3: 实现（注册命令）**

在 `gzh/gzh/cli.py`：
- 顶部 import 区追加：
```python
from gzh.notify import send_telegram
```
- 在 `triage_skip_cmd` 定义之后、`def main():` 之前追加：
```python
@cli.group("notify")
def notify_group():
    """Send result notifications (e.g. telegram)."""


@notify_group.command("telegram")
@click.option("--message", "-m", required=True)
@click.option("--chat", "chat_id", default=None, help="override TELEGRAM_CHAT_ID")
def notify_telegram_cmd(message, chat_id):
    """Send a message via Telegram bot (token/chat from env)."""
    res = send_telegram(message, chat_id=chat_id)
    click.echo(_json.dumps(res, indent=2, ensure_ascii=False))
    # non-fatal: never exit non-zero (notification is auxiliary)
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `cd gzh && python -m pytest tests/test_notify.py -v && python -m pytest -q`
Expected: test_notify.py 全 PASS（6 passed）；全量无回归。

- [ ] **Step 5: 手动验证**

Run: `gzh notify telegram --message hi`（无 env）→ 输出 `{"ok": false, "error": "TELEGRAM_BOT_TOKEN not set; skipped"}`，退出码 0。

- [ ] **Step 6: Commit**

```bash
git add gzh/gzh/cli.py gzh/tests/test_notify.py
git commit -m "feat(gzh): register notify telegram command (non-fatal on missing token)"
```

---

### Task 5: `gzh-bump-from-issues` skill 文档

**Files:**
- Create: `.agents/skills/gzh-bump-from-issues/SKILL.md`

**Interfaces:** skill 通过 opencode `skill` 工具按需加载；引用 `gzh bump-issues`、`gzh triage`、`gzh notify`、`gzh-version-bump` skill 的 finish-pipeline。

- [ ] **Step 1: 写 `SKILL.md`（frontmatter 遵守 opencode 规则，name=`gzh-bump-from-issues`）**

```markdown
---
name: gzh-bump-from-issues
description: "Orchestrate batch version-bump from nvchecker bump-reminder issues. Trigger on '处理 bump issue 队列', '批量 bump', 'triage open nvchecker issues', or when many packages need bumping. Reads gzh bump-issues queue, evaluates each (maintainer/comments/version-gap/type), bumps the viable ones via gzh-version-bump (local commit only), records skips to triage log, summarizes, optionally notifies via telegram. gentoo-zh overlay only; does not auto-push/PR."
---

# gzh-bump-from-issues — 从 nvchecker issue 队列半自动批量 bump

编排 `gzh bump-issues`（发现）与 `gzh-version-bump`（执行），产出每包独立分支的本地 commit + 持久决策记录 + 汇总报告。**不自动 push/PR**。

## 前置约束（见 AGENTS.md）
- `~arch` only、一包一分支一 commit、commit 无 AI 署名。
- ebuild 写法权威：`docs/devmanual.md`。
- 用 `gzh` 工具，不现想 bash。重试上限 3 次。

## 4 阶段流程

### 阶段 1：取队列 + 排除已跳过
1. `gzh bump-issues`（默认或 `--maintainer <gh-user>`/`--pkg` 过滤）→ JSON 队列（含 comments）。
2. `gzh triage list` → 已跳过 issue 列表。
3. 从队列**排除**已在 triage 的 issue（按 `issue` 号）。

### 阶段 2：综合评估（agent 判断，逐个 issue）
看每个剩余 issue，输出 bump/skip + 理由：
- **maintainer**：body `CC: @<name>` 是否当前 `gh api user --jq .login`。非自己的不强制 skip（gentoo-zh 允许协作），但识别责任边界。
- **comments 阻塞信号**：扫 `comments[].body` 关键词（`crash`/`broken`/`regression`/`build fail`/`不要升级`/`don't bump`）→ 强 skip。
- **版本跨度**：`target_version` vs 当前 ebuild PV（`gzh ebuild-parse`）。major 跨跃 → 标记「需查上游 changelog」，A5 加权。
- **包类型**：`-bin`（SRC_URI 为主）/ 源码（依赖+patch 风险，A5/A6 加权）。

### 阶段 3：循环处理
- **skip 的** → `gzh triage skip <issue> --cat-pkg <p> --target-version <v> --reason "<理由>"`。
- **bump 的** → 每包独立分支（`git fetch origin && git checkout -b <cat>-<pn>-<ver> origin/master`），按 **gzh-version-bump** skill 的 A→收尾全流程，**到本地 commit 为止**：
  - A1-A8 + 收尾（`gzh lint`/`manifest`/`pkgcheck`/`build-test`/`diff-ebuild`/`commit`）。
  - 硬门失败（manifest/patch/build-test）→ **记失败**（汇总：cat/pkg + phase + error + 诊断分支），**不写 triage skip**（尝试失败 ≠ 主动跳过），build-test 失败提示「转 fix-build-failure（未实现）」。
  - 重试上限 3 次：同包同错重复 2 次即停、记失败、继续下一个。

### 阶段 4：汇总 + 回报
1. 写汇总到 `.gzh/bump-batch-<时间戳>.md`：成功（cat/pkg-ver + 分支 + issue）/ 失败（cat/pkg + phase + error + 分支）/ 跳过（cat/pkg + issue + reason，已记 triage）+「下一步：手动 PR」命令模板（`gh pr create --repo Gentoo-zh/gentoo-zh --base master --head $(gh api user --jq .login):<branch>`）。
2. 若 `TELEGRAM_BOT_TOKEN` env 配置：`gzh notify telegram --message "<成功N/失败N/跳过N + 分支列表>"`；否则跳过。

## 排除
- 自动 push/PR（停本地 commit，用户手动 PR）。
- fix-build-failure（失败只记录+提示）。
```

- [ ] **Step 2: 校验 skill name 合规 + frontmatter**

Run:
```bash
python3 -c "import re,pathlib; d=pathlib.Path('.agents/skills/gzh-bump-from-issues'); assert d.is_dir() and d.name=='gzh-bump-from-issues'; t=(d/'SKILL.md').read_text(); assert t.startswith('---\n'); fm=t[3:].split('\n---\n')[0]; name=[l for l in fm.splitlines() if l.startswith('name:')][0].split(':',1)[1].strip(); assert name=='gzh-bump-from-issues', name; assert re.match(r'^[a-z0-9]+(-[a-z0-9]+)*\$', name); desc=[l for l in fm.splitlines() if l.startswith('description:')][0]; assert 0<len(desc)<=1100; print('skill ok, desc len', len(desc))"
```
Expected: `skill ok, desc len <number>`

- [ ] **Step 3: Commit**

```bash
git add .agents/skills/gzh-bump-from-issues
git commit -m "feat(skill): add gzh-bump-from-issues orchestration skill"
```

---

### Task 6: L3 端到端验证

**Files:** 无（运行验证）

**Interfaces:** 对真实 open issue + triage + notify 跑通编排。

- [ ] **Step 1: triage 命令真实验证**

Run（在 skills 仓库根）:
```bash
gzh triage skip 99999 --cat-pkg test/x --target-version 1.0 --reason "L3 probe"
gzh triage list --pkg test/x
```
Expected: list 命中 issue 99999；`triage/skip-log.jsonl` 在 skills 根创建（gitignored? 否——triage/ 是 tracked，但此 probe 记录应随后 `git checkout` 或手动删，避免污染。**验证后 `rm triage/skip-log.jsonl`**）。

- [ ] **Step 2: notify 命令真实验证（无 env，非崩溃）**

Run: `gzh notify telegram --message "L3 probe"; echo "exit=$?"`
Expected: 输出 `{"ok": false, "error": "TELEGRAM_BOT_TOKEN not set; skipped"}`，`exit=0`。

- [ ] **Step 3: 编排真实验证（1 个真实 open issue）**

选一个 open nvchecker issue（如 `dev-python/fuo-ytmusic` 0.4.18，issue 号由 `gzh bump-issues --pkg dev-python/fuo-ytmusic` 取）：
1. `gzh bump-issues --pkg dev-python/fuo-ytmusic` → 确认队列含目标版本。
2. 按 `gzh-bump-from-issues` skill 的 4 阶段跑一遍（agent 手动编排）：
   - 评估（fuo-ytmusic 源码包，跨距 0.4.2→0.4.18 大 → 谨慎查依赖；无 comments 阻塞 → bump）。
   - 跑 gzh-version-bump 流程到本地 commit（独立分支）。
3. 确认：成功分支 + commit 存在。
4. 再对 1 个有 comments 阻塞信号的 issue（若队列里有）评估为 skip → `gzh triage skip` → `triage/skip-log.jsonl` 有记录。
5. 汇总报告生成（`.gzh/bump-batch-*.md`）。

- [ ] **Step 4: 若 L3 发现缺陷**

修复并补单测（同前：缺陷 → 失败测试 → 修复 → 全量绿 → commit）。若 build 因非 root 失败（install phase），按已知环境限制标注、不阻断（build-test 可降级 quick/none 注明）。验证后清理 probe 产生的 `triage/skip-log.jsonl` 测试记录（或保留作示例）。

---

## Self-Review

**1. Spec coverage（spec §12 验收）：**
- 验收#1（triage skip/list 往返）：Task 1 `test_skip_appends_and_list_roundtrip` + Task 2 `test_triage_skip_and_list_via_cli` + Task 6 Step 1 ✅
- 验收#2（notify 缺 token 非崩溃）：Task 3 `test_missing_token_skips` + Task 4 `test_notify_telegram_missing_token_exits_zero` + Task 6 Step 2 ✅
- 验收#3（L3 真实编排：成功分支+commit、skip 进 triage、汇总）：Task 6 Step 3 ✅
- 验收#4（L1 全绿、去个人化）：Task 1-4 全 mock 单测；env/动态 gh user 无硬编码（grep 验证）✅
- §5 triage 数据格式（JSONL/字段/skip 追加/list 过滤）：Task 1 ✅
- §9 notify（env token/chat/缺省跳过/Markdown parse_mode）：Task 3 ✅
- §6/§7/§8（评估/失败/汇总）：agent 行为，Task 5 skill 文档指导 + Task 6 L3 验证 ✅

**2. Placeholder scan：** 无 TBD/TODO；每步含完整测试代码、实现代码、命令、预期。✅

**3. Type consistency：** `list_skipped(log_path, pkg=None) -> list[dict]`、`skip_issue(log_path, issue:int, cat_pkg, target_version, reason) -> dict`、`send_telegram(message, chat_id=None, token=None, client=httpx) -> dict` 在 Task 1-4 间一致；triage 记录字段 `{issue, cat_pkg, target_version, reason, skipped_at}` 在 list/skip 一致；notify 返回 `{ok, ...}` 在 send/cli 一致；cli group 名 `triage`/`notify` 与 Task 5 skill 文档引用一致。✅
