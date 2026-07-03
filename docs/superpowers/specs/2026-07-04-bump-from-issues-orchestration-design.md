# `gzh-bump-from-issues` 编排闭环 — 设计文档

- **日期**: 2026-07-04
- **状态**: Draft（待用户 review）
- **仓库**: `Gentoo-zh/skills`
- **范围**: 路线图阶段 3「自动值守」的半自动雏形 —— 新 `gzh-bump-from-issues` skill + 两个 gzh 命令（`gzh triage` 跳过记录、`gzh notify telegram` 回报），把 `gzh bump-issues`（发现层）与 `gzh-version-bump` skill（执行层）编排成闭环。
- **前置**: 阶段 0 MVP（`gzh` 11 子命令 + `gzh-version-bump` skill）、阶段 2 第一刀（`gzh bump-issues` 读 nvchecker issue 列队列）均已交付并合并 master。

---

## 1. 背景与目标

`gzh bump-issues` 已能列出 Gentoo-zh/gentoo-zh 上约 22 个 open nvchecker bump reminder issue（含 comments）。`gzh-version-bump` skill 已能对单个包跑完整 bump 流程。**缺口**：两者之间没有编排层——维护者仍需逐个手动触发 gzh-version-bump，且跳过决策无持久记录（下次又从头评估）。

`gzh-bump-from-issues` 要做的：agent 读 bump-issues 队列 → 综合评估每个 → 对合适的调 gzh-version-bump（到本地 commit）→ 跳过/失败持久记录 → 汇总 + 可选 telegram 回报。形成「nvchecker CI → issue → 评估 → bump → 本地 commit → 用户 review → 手动 PR → 关 issue」的半自动闭环。

**核心价值**：把 22 个待 bump issue 从「纯人工」提升到「agent 预处理 + 持久决策记录 + 人工只 review/PR」；跳过记录避免重复评估、可追溯。

---

## 2. 设计原则

| 原则 | 落实 |
|---|---|
| 脚本给数据，agent 做判断 | 编排/评估/决策是 agent 工作（skill）；跳过记录读写、通知发送是确定性强数据操作（gzh 命令） |
| 去个人化 | telegram token/chat、PR head、maintainer 全动态或 env；无 `liangyongxiang` 硬编码 |
| 安全边界 | 每包独立分支；bump 到**本地 commit 为止**，不自动 push/PR；不碰 `/var/db/repos` |
| 确定性可单测 | gzh triage / gzh notify 全 mock（文件/httpx），pytest 覆盖；评估/失败/汇总靠 L3 |
| 复用 | 不改 gzh-version-bump skill；复用其 finish-pipeline + 11 个 gzh 子命令 |

---

## 3. 整体架构

三件套：

1. **新 skill `gzh-bump-from-issues`**（`.agents/skills/gzh-bump-from-issues/SKILL.md`）：agent 编排——取队列、综合评估、循环 bump、汇总、回报。无代码，纯文档指导。
2. **新 gzh 命令 `gzh triage`**：跳过记录的确定性强读写（`triage/skip-log.jsonl`），可单测。
3. **新 gzh 命令 `gzh notify telegram`**：结果回报（env 配置，可选），可单测。

**为何 skill + gzh 命令混合**：评估/决策/失败处理是判断（agent）；跳过记录与通知发送是确定性数据/IO（gzh 命令，可单测、可复用）。gzh 不调 agent skill（确定性工具不调判断层），skill 调 gzh 命令。

---

## 4. skill 主流程（4 阶段）

1. **取队列**：`gzh bump-issues`（默认或带 `--maintainer`/`--pkg` 过滤）→ JSON 队列（含 comments）；`gzh triage list` → 已跳过 issue 列表 → 从队列排除。
2. **综合评估**：agent 对剩余 issue 逐个判断 bump/skip（细则见 §6），输出决策 + 理由。
3. **循环 bump**：
   - skip 的 → `gzh triage skip <issue> --cat-pkg <p> --target-version <v> --reason <text>` 记录。
   - bump 的 → **每包独立分支**（`category-package-${VERSION}`，从 `origin/master` 切），调 gzh-version-bump skill 的 A→收尾全流程，**到本地 commit 为止**（不自动 PR）。失败处理见 §7。
4. **汇总 + 回报**：生成汇总报告（§8）；env 配置则 `gzh notify telegram --message <摘要>` 回报（§9）；提示用户手动 PR。

---

## 5. triage：数据格式 + `gzh triage` 命令

**数据文件**：`triage/skip-log.jsonl`（skills 仓库 tracked，commit）。位置：`find_overlay_root()/triage/skip-log.jsonl`（在 skills 仓库跑 → skills 根/triage/）。文件不存在自动创建（含 header 注释行可选）。

**每行 JSON**：
```json
{"issue": 10588, "cat_pkg": "net-proxy/v2rayA", "target_version": "2.4.6", "reason": "comments 报 crash，待上游修复", "skipped_at": "2026-07-04T05:46:36Z"}
```

**命令接口**：
```
gzh triage list [--pkg <cat/pkg>]            # 读 skip-log，输出 JSON 数组
gzh triage skip <issue> --cat-pkg <cat/pkg> --target-version <ver> --reason <text>
                                             # 追加一行（重复 issue 允许，记录多次评估）
```
- `list`：读 jsonl，解析每行 JSON，可选 `--pkg` 过滤，输出数组。
- `skip`：追加一行（`skipped_at` = 当前 ISO 时间），文件不存在则创建。
- 退出码：成功 0；坏行/非 dict 行静默跳过（不报错、不 exit 1）。

---

## 6. 评估细则（agent 综合评估）

agent 对未跳过的 issue 逐个判断，维度：

- **maintainer**：issue body 的 `CC: @<name>` 是否当前 `gh api user`。非自己的不强制 skip（gentoo-zh 允许协作 bump），但 agent 应识别责任边界。
- **comments 阻塞信号**：扫 `comments[].body` 关键词（`crash`/`broken`/`regression`/`build fail`/`不要升级`/`don't bump`）→ 强 skip 信号（在 triage reason 注明「comments 报 X，待上游修」）。
- **版本跨度**：`target_version` vs 当前 ebuild PV（`gzh ebuild-parse`）。大跨度（major 跳跃，如 0.4.2→0.4.18）→ 标记「需重查上游 changelog」，A5 依赖评估加权。
- **包类型**：`-bin`（SRC_URI 为主，简单）/ 源码（依赖 + patch 风险，A5/A6 加权）。

**输出**：bump/skip + 理由。skip 的理由写入 `gzh triage skip --reason`。评估是 agent 判断，无单测，靠 L3 验证。

---

## 7. 失败处理

bump 过程中硬门失败（manifest fetch 失败 / patch 不适用 / build-test 失败）：
- **记失败**（汇总报告里：`cat/pkg + phase + error + 诊断分支名`），**不写 triage skip**（尝试失败 ≠ 主动跳过——下次可重试）。
- 诊断分支保留（供 `git diff`/重试），不删。
- build-test 失败 → 汇总提示「转 fix-build-failure（未实现）」。
- **重试上限 3 次**（AGENTS.md）：同包同错重复 2 次即停该包、记失败、继续下一个。

---

## 8. 汇总格式

写 `.gzh/bump-batch-<时间戳>.md`（ignored）+ stdout 摘要：

```markdown
# gzh-bump-from-issues 批次 <时间戳>

## 成功（N）
- media-fonts/sarasa-gothic-1.0.40  branch=media-fonts-sarasa-gothic-1.0.40  issue=#10581

## 失败（N）
- net-proxy/foo  phase=build-test  error=compile fail  branch=net-proxy-foo-1.2.3

## 跳过（N）  [已记 triage/skip-log.jsonl]
- net-proxy/v2rayA  issue=#10588  reason=comments 报 crash，待上游修复

## 下一步
手动 PR（每个成功分支）:
  gh pr create --repo Gentoo-zh/gentoo-zh --base master --head $(gh api user --jq .login):<branch>
```

---

## 9. 通知（telegram bot 回报）

**命令**：
```
gzh notify telegram --message <text> [--chat <chat_id>]
```
- **配置**：`TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` **环境变量**（secret 不入仓、去个人化）；`--chat` 覆盖 chat。
- **实现**：`httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text, "parse_mode": "Markdown"})`，`raise_for_status`。
- **缺 token**：返回 `{"ok": false, "error": "TELEGRAM_BOT_TOKEN not set; skipped"}`，**非崩溃**（skill 据此静默跳过通知）。
- **skill 调用**：汇总后若 env 配置则调 `gzh notify telegram --message <汇总摘要（成功N/失败N/跳过N + 分支列表）>`。
- **为何 gzh 命令**：发送是确定性 IO，沉淀可单测、可复用（其他命令/skill 也能 notify）；token 走 env 不碰 git。

---

## 10. 去个人化与安全边界

**去个人化：**
- `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` env；PR head `$(gh api user --jq .login)`；maintainer 识别用 `gh api user`。无 `liangyongxiang` 硬编码。
- triage log 在 skills 仓库 `triage/`（组织仓库，非个人路径）。

**安全边界：**
- **不自动 push/PR**：每包到本地 commit 为止；PR 命令在汇总里作为**提示**给用户手动执行。
- 不碰 `/var/db/repos/gentoo-zh`（synced 副本）；每包在开发副本独立分支。
- `gzh notify` 仅发 Telegram 消息（read-only 语义的 write：只 sendMessage，不碰 bot 配置）。
- commit 无 AI 署名（继承 gzh-version-bump/AGENTS.md）。

---

## 11. 测试策略

| 层 | 测什么 | 怎么测 |
|---|---|---|
| **`gzh triage` L1** | list 读取/`--pkg` 过滤、skip 追加、文件自动创建、重复 skip、JSONL 格式、坏行处理 | pytest + `tmp_path` 文件 fixture；纯文件 IO，无网络/gh |
| **`gzh notify telegram` L1** | 发送成功（mock httpx 200）、缺 token 跳过、Telegram API 错误（mock 非 200） | pytest + monkeypatch `httpx.post`；不真实发送 |
| **评估/失败/汇总 L3** | skill 全流程真实跑通 | 选 1-2 个真实 open issue 跑编排：成功包有分支+commit、跳过包进 triage/skip-log.jsonl、汇总报告生成、（env 配置时）telegram 收到消息 |

**L2 skill 触发不在本刀范围**（gzh-bump-from-issues 触发靠 opencode skill 发现，量化评估后置）。

---

## 12. 交付边界与验收

**包含：**
- `gzh/gzh/triage.py`（`list_skipped`/`skip_issue` 纯函数 + JSONL 读写）+ `gzh triage` cli 注册 + L1 单测
- `gzh/gzh/notify.py`（`send_telegram` httpx 封装）+ `gzh notify telegram` cli 注册 + L1 单测
- `.agents/skills/gzh-bump-from-issues/SKILL.md`（4 阶段编排指导）
- 无新文档（复用 devmanual/AGENTS.md）；gzh-version-bump skill 不改

**不包含：** 自动值守（定时触发）、自动 PR、fix-build-failure skill（失败只记录+提示）、其他通知后端（Slack/邮件）、L2 触发量化。

**验收：**
1. `gzh triage skip 99999 --cat-pkg a/b --target-version 1 --reason test` → 追加一行；`gzh triage list --pkg a/b` 命中。
2. `gzh notify telegram --message hi`（无 env）→ `ok=False` + token 缺失提示，非崩溃。
3. L3：对 1 个真实 open issue（如 `dev-python/fuo-ytmusic` 0.4.18）跑 gzh-bump-from-issues 编排 → 成功分支 + commit；对 1 个 comments 含 crash 信号的 issue 评估为 skip → triage/skip-log.jsonl 有记录；汇总报告生成。
4. L1 全绿；去个人化（env/动态 gh user，无硬编码）。

---

## 13. 后续（独立刀）

- **fix-build-failure skill**（阶段 1）：build 失败时真正诊断修复，当前编排只记录+提示。
- **自动值守**（阶段 3）：定时触发 gzh-bump-from-issues（cron/CI），含 PR 自动化（当前停本地 commit）。
- **其他通知后端**：Slack/邮件/Server酱（`gzh notify` 扩展 `--backend`）。
- **评估 heuristics 代码化**：当评估规则稳定，把关键词扫描/版本跨度计算沉淀为 `gzh triage assess`（确定性辅助），agent 做最终判断。
