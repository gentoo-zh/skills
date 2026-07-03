# `gzh bump-issues` — 扫描发现层（读 nvchecker bump issue）设计

- **日期**: 2026-07-04
- **状态**: Draft（待用户 review）
- **仓库**: `Gentoo-zh/skills`
- **范围**: 路线图阶段 2「扫描发现层」的第一刀 —— 仅落地 `gzh bump-issues`（读 bump reminder issue 列队列）。`gzh outdated`（本地 nvcmp 全量扫描）与 `gzh drop-old`（旧版本清理）属后续独立刀，不在本设计内。
- **前置**: 阶段 0 MVP 已交付（`gzh` 11 子命令 + `version-bump` skill，合并 master，经真实 PR #10720 验证）。

---

## 1. 背景与目标

gentoo-zh 已有完整上游检测基础设施：nvchecker CI（`.github/workflows/nvchecker.yml`）比对当前 ebuild 与上游，对过期包在 `Gentoo-zh/gentoo-zh` 开 **bump reminder issue**（label `nvchecker`，标题 `[nvchecker] <cat/pkg> can be bump to <version>`）。当前这类 open issue 约 25 个。缺口在于：**issue 开出后，bump 动作仍纯人工**——维护者需手动逐个翻阅 issue、判断是否可 bump、再跑 version-bump。

`gzh bump-issues` 要做的，是把"翻阅 issue、提取待 bump 信息、含 LLM 分析所需的回复上下文"这一**确定性、可重复**的动作沉淀为一条命令，输出结构化 JSON 队列，交由 agent（version-bump skill）或维护者判断与执行。

**核心价值**：闭环 nvchecker CI → issue → bump → PR → 关 issue 的"发现"环节；把 issue（含回复）结构化，便于 LLM 后续分析（回复常含 build 失败细节、维护者讨论、特殊说明）。

---

## 2. 设计原则

| 原则 | 落实 |
|---|---|
| 脚本给数据，agent 做判断 | `gzh bump-issues` 仅列队列（只读），不触发 bump；bump 由 version-bump skill 编排 |
| 去个人化 | 仓库默认 `Gentoo-zh/gentoo-zh`（组织固定名）+ `--repo` 覆盖；不硬编码任何个人路径/owner |
| 纯只读、零副作用 | 仅 `gh api graphql` 查询，不改 issue、不 push、不 PR、不碰 `/var/db/repos` |
| 确定性可单测 | gh 输出全 mock，pytest 覆盖解析/过滤/错误路径 |
| 减调用、抗增长 | GraphQL 单次批量（issues + comments 一次拿），N+1 压成 1 |

---

## 3. 命令接口

```
gzh bump-issues [--repo Gentoo-zh/gentoo-zh] [--state open|all|closed]
                [--maintainer <name>] [--pkg <cat/pkg>]
                [--comments/--no-comments] [--limit 100]
```

| 选项 | 默认 | 用途 |
|---|---|---|
| `--repo` | `Gentoo-zh/gentoo-zh` | 目标仓库（owner/repo），可覆盖 |
| `--state` | `open` | issue 状态过滤 |
| `--maintainer` | （不过滤） | 按 issue body 的 `CC: @<name>` 过滤（去 `@`） |
| `--pkg` | （不过滤） | 按 `<cat/pkg>` 精确过滤 |
| `--comments` | **on** | 拉取每个 issue 的回复；`--no-comments` 跳过（仅标题+body，快速列表） |
| `--limit` | `100` | 最多列出多少条（GitHub GraphQL `first:` 上限 100） |

**退出码**：成功（含空队列）= 0；gh 调用失败 = 1；gh 未装/未认证 = 2。

---

## 4. 输出 schema

```json
[
  {
    "issue": 10581,
    "cat_pkg": "media-fonts/sarasa-gothic",
    "target_version": "1.0.40",
    "oldver": "1.0.39",
    "maintainer": "Linerre",
    "title": "[nvchecker] media-fonts/sarasa-gothic can be bump to 1.0.40",
    "url": "https://github.com/Gentoo-zh/gentoo-zh/issues/10581",
    "state": "open",
    "comments": [
      {"author": "microcai", "body": "...", "created_at": "2026-07-01T00:00:00Z"}
    ],
    "comments_truncated": false
  }
]
```

- 缺失字段（无 `oldver`/无 `CC`）为 `null`，不省略键。
- `comments`：数组，每项 `{author, body, created_at}`；无回复为 `[]`。
- `comments_truncated`：该 issue 回复 >50 时为 `true`（GraphQL `first:50` 截断）。
- cli 输出 JSON **对象** `{"ok": true, "results": [...], "skipped": N}`（与 §5、`manifest`/`pkgcheck` 等命令风格一致）；上方 schema 是 `results` 数组中每个 item 的结构。失败时对象含 `error`/`stderr` 而非 `results`。`skipped`（标题不匹配被跳过的计数）随对象输出，便于脚本消费。

---

## 5. 数据流与后端

```
gzh bump-issues
  └─ run_bump_issues(opts, runner=subprocess.run) -> dict
       ├─ 构造 GraphQL 查询（labels:["nvchecker"], states, first:limit, comments first:50）
       ├─ runner(["gh","api","graphql","-f","query=<q>"], capture)   # 复用 gh 认证
       ├─ 解析 GraphQL JSON → 扁平 issue 节点列表
       ├─ 逐节点：标题正则解析 → {cat_pkg, target_version}；body 解析 → {oldver, maintainer}
       ├─ 应用 --maintainer / --pkg 过滤
       └─ 返回 {"ok": true, "results": [...], "skipped": N}
```

**后端**：`gh api graphql`（gh 子命令），复用 gh 已有认证，无新 Python 依赖。GraphQL 单次查询同时取 issues + 各自 comments，把 REST 的 1+N 调用压成 1。

**GraphQL 查询（形如）：**
```graphql
query {
  repository(owner: "Gentoo-zh", name: "gentoo-zh") {
    issues(labels: ["nvchecker"], first: 100) {
      nodes {
        number title body state url
        author { login }
        comments(first: 50) {
          nodes { author { login } body createdAt } } } } } }
}
```
> `states` 参数按 `--state` 拼（`open`→`OPEN`、`closed`→`CLOSED`；`all` 不传 states；GitHub issue 只有 OPEN/CLOSED 两态，无 MERGED）。

**为何 GraphQL 而非 REST**：减调用（1 vs 1+N）、抗未来增长、单响应含 comments 嵌套。gentoo-zh 当前 ~25 issue、评论稀疏，单页（first:100 issues、first:50 comments；GitHub GraphQL `first` 上限 100）足够，MVP 不实现分页。

**为何 `gh api graphql` 而非 python GraphQL 库**：gh 已认证（刚用其开过 PR #10720），与 gzh 的 commit/PR 走 gh/pkgdev 一致，零 token 管理、零新依赖。

---

## 6. 解析规则

**标题**（权威字段，来自 nvchecker CI 固定模板）：
- 正则：`^\[nvchecker\]\s+(\S+)\s+can be bump to\s+(\S+)$`
- group(1) → `cat_pkg`（如 `media-fonts/sarasa-gothic`、`net-proxy/naiveproxy-bin`）
- group(2) → `target_version`（如 `1.0.40`、`150.0.7871.63_p1`）
- **不匹配 → 跳过该 issue**（可能是手动贴了 nvchecker 标签的非 reminder issue），计入 `skipped`，不计错误。

**body**（多行，字段顺序不保证）：
- `oldver:\s*(\S+)` → `oldver`
- `CC:\s*@?(\S+)` → `maintainer`（允许带或不带 `@`）
- 均可选，缺失为 `null`。

**comments**：GraphQL 已结构化，直接映射 `{author, body, created_at}`，无需正则。

---

## 7. 错误处理

| 情况 | 处理 | 退出码 |
|---|---|---|
| gh 未装 / 未认证（`gh auth status` 失败） | stderr 明确提示"需先 `gh auth login`" | 2 |
| gh 调用非 0 退出（网络/配额/GraphQL errors） | 输出 `{"ok": false, "error": "...", "stderr": "..."}` | 1 |
| GraphQL `errors` 字段非空 | 同上，把 errors 串进 `error` | 1 |
| 标题不匹配正则 | 跳过，计入 `skipped`，stderr 打印计数 | 0 |
| comments >50 | `comments_truncated: true`，截断到 50 | 0 |
| 空队列（无 open nvchecker issue） | 正常返回 `[]` | 0 |

不重试（重试上限 3 次是 skill 层运行时策略，工具本身不重试，与 MVP 一致）。

---

## 8. 测试策略（L1 pytest，全 mock）

gh 输出全部用 fixtures + `runner=` 注入（同 MVP 的 `run_manifest`/`run_pkgcheck` 模式），**不真实连 GitHub**（CI 离线/配额/不稳定）。

| 测试 | 覆盖 |
|---|---|
| `test_parse_title_typical` | `media-fonts/sarasa-gothic` / `1.0.40` |
| `test_parse_title_version_variants` | `-bin` 包、`_p1`/`_rc` 版本号、多段版本 |
| `test_parse_title_unmatched_skipped` | 畸形标题 → 跳过，计 `skipped` |
| `test_parse_body_fields` | `oldver` + `CC @x` 提取；缺失字段为 null |
| `test_graphql_to_queue` | 一段 mock GraphQL JSON → 正确队列（含 comments 映射） |
| `test_comments_truncated` | 51 条评论 → 取 50 + `comments_truncated:true` |
| `test_filter_maintainer` / `test_filter_pkg` | 过滤命中与排除 |
| `test_no_comments_option` | `--no-comments` → comments 为 `[]`、不发 comments 子查询 |
| `test_gh_failure` | mock gh 非 0 退出 → `ok:false` + 退出码 1 |
| `test_gh_not_authenticated` | mock `gh auth status` 失败 → 退出码 2 |

**L2/L3**：skill 触发不在本刀范围（bump-issues 是 gzh 子命令，非 skill）。真实端到端：在 L3 冒烟里对 `Gentoo-zh/gentoo-zh` 真实跑一次，确认队列非空且含 #10581。

---

## 9. 去个人化与安全边界

**去个人化：**
- `--repo` 默认 `Gentoo-zh/gentoo-zh`（组织仓库固定名，非个人路径），可覆盖。
- 无任何 `liangyongxiang` / 个人 fork owner 硬编码。
- 认证复用 gh（用户自己 `gh auth login`），gzh 不持有 token。

**安全边界（纯只读）：**
- 仅 `gh api graphql` 查询（read-only），不调任何 mutation。
- 不修改 issue（不评论/不关闭）、不 push、不开 PR、不改 overlay 文件、不碰 `/var/db/repos`。
- bump 动作由 version-bump skill 在显式触发时执行，本命令不联动。

---

## 10. 与 version-bump skill 的衔接

松耦合，本刀不改 version-bump：

1. `gzh bump-issues` 产出队列 JSON（含 issue 号、cat/pkg、目标版本、maintainer、comments）。
2. agent / 维护者选定一个包，调 version-bump skill 走 A→收尾全流程（version-bump 的 A1 `upstream-version` 会实时复核上游版本，与 issue 快照目标通常一致）。
3. version-bump 的 `finish-pipeline.md` 已含 `gh pr create --head $(gh api user --jq .login):<branch>` 与 PR body `closes #<issue>` 的格式 —— 完成后 PR 自动关 issue。

即：bump-issues 负责"发现 + 结构化"，version-bump 负责"执行 + 交付"，两者通过 issue 号衔接，无新 skill。

---

## 11. 交付边界与验收

**包含：**
- `gzh/bump_issues.py`（`run_bump_issues()` 纯函数 + GraphQL 构造/解析/过滤）
- `gzh/cli.py` 注册 `bump-issues` 子命令
- `gzh/tests/test_bump_issues.py`（L1 全 mock）
- 无新文档（复用 `docs/devmanual.md` / `AGENTS.md`）；SKILL 不变

**不包含：** `gzh outdated`（nvcmp 全量）、`gzh drop-old`、bump 编排 skill、自动值守、webhook/通知、GraphQL 分页。

**验收：**
1. `gzh bump-issues`（默认参数）真实跑一次 → 返回非空 JSON 队列，含 `media-fonts/sarasa-gothic` → 1.0.40（issue #10581）。
2. `--maintainer Linerre` 过滤命中含 `CC: @Linerre` 的子集。
3. `--no-comments` 不发 comments 子查询、输出 `comments: []`。
4. `gzh bump-issues --pkg media-fonts/sarasa-gothic` 单包命中。
5. L1 pytest 全绿；gh 失败路径退出码正确。
6. 去个人化：代码无个人路径硬编码；`--repo` 可指向其他仓库。

---

## 12. 后续（独立刀）

- `gzh outdated [--owner] [--pkg]`：本地 nvcmp 全量扫描，列过期包报告（偏 CI/手动全量，与读 issue 互补）。
- `gzh drop-old <cat/pkg>`：按规则（保留 N 个最新版 / 按时间）清理旧版本，补 version-bump「只 add 不 drop」。
- GraphQL 分页：当 nvchecker open issue >200 或单 issue 评论 >50 时启用（当前规模非必要）。
- bump 编排 skill（阶段 3 自动值守）：读 bump-issues 队列 → 循环 version-bump，含失败汇总。
