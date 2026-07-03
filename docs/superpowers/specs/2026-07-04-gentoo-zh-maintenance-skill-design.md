# Gentoo-zh 包维护 Skill 套件 — 设计文档

- **日期**: 2026-07-04
- **状态**: Draft（待用户 review）
- **仓库**: `Gentoo-zh/skills`（原 `gentoo-zh-skills`，已改名）
- **目标平台**: opencode skill（兼容 Claude Code）

---

## 1. 背景与目标

`gentoo-zh` 是 Gentoo 中文社区的第三方 overlay（`Gentoo-zh/gentoo-zh`），含数千 ebuild。其核心铁律为 **"DO NOT BREAK PEOPLE'S SYSTEM"**：只用 `~arch` 关键字、提交前必须 `pkgcheck scan`、用 `pkgdev commit` 生成规范 commit message、协作以 PR 形式。

本仓库（`Gentoo-zh/skills`）的目标是提供一套**组织级、可被所有维护者（及其 AI agent）共享**的 skill，自动化 gentoo-zh 包维护，核心价值是**减少重复劳动**。

**分两阶段：**
- **第一阶段**：聚焦**现有包的维护**（版本升级、修复编译/运行失败、QA/CI 修复、EAPI/依赖更新）。
- **第二阶段**：处理**新包**；以及扫描发现、自动值守等上层能力。

本设计覆盖第一阶段，并给出 MVP 最小切片与后续路线图。

---

## 2. 设计原则

本设计遵循以下工程原则，每条对应具体选择：

| 设计原则 | 具体选择 |
|---|---|
| 确定性流程须可单测、可维护、易诊断 | 确定性工具用 **Python**（`gzh` CLI 包）实现，配 pytest 单测 |
| 每个 skill 单一职责、触发准确 | **分层 skill**：场景子 skill 各自聚焦；收尾流程内嵌，不做单 skill 巨型 mode dispatch |
| 组织级仓库须对所有维护者可用 | **去个人化**（见 §10）：路径/fork/maintainer/owner/测试环境全部参数化或动态获取 |
| 不破坏用户系统 | 🔴硬门强制校验，绝不带病提交（见 §7） |

**采纳的 gentoo-zh 社区既有实践（经实际维护验证有效的约定）：**
- Working rules：`~arch` only、一包一分支一 PR、commit 无 AI 署名、`pkgcheck scan --commits --net`、CI 修复 squash 回原 commit。
- 领域知识：rust/tauri/npm/python 打包要点、fix-patterns、ci-troubleshooting、bump-playbook。
- 工程实践：**重试上限 3 次**（同一错误重复 2 次即停）；nvchecker 按 owner 查 bump reminder issue。
- 验证流程顺序：`pkgdev manifest` → `pkgcheck scan` → emerge `--onlydeps` → `ebuild prepare`(patch test) → `ebuild install`。

---

## 3. 整体架构（方案 B：分层 skill + `gzh` Python CLI）

**核心思想：脚本给数据，agent 做判断。**

- **确定性、可重复的动作**（生成 Manifest、跑 pkgcheck、查上游版本、解析 ebuild）沉淀为 Python 工具 `gzh`，每条命令返回**结构化 JSON**。
- **需要理解/判断的动作**（改 ebuild 内容、诊断编译失败、评估 patch 适用性）由 **skill 指导 agent** 完成，所有 ebuild 写法以 **devmanual 为唯一权威**。
- **场景子 skill** 只写自己场景的**独特判断**，收尾流程（校验→Manifest→QA→验证→提交）统一调用 `gzh`，不重复实现。

**分阶段交付**（与"三模式都要"对齐，按依赖排序）：
1. **阶段 0 / MVP**：执行器核心（`gzh` + 收尾流程）+ `gzh-version-bump` 子 skill
2. **阶段 1**：补齐 `fix-build-failure` / `qa-fix` / `eapi-deps` / `create` 子 skill
3. **阶段 2**：扫描发现层（`gzh outdated`、读 bumpbot issue、`drop-old` 定时任务）
4. **阶段 3**：自动值守 + `--pr` 自动化 + 容器测试 backend

---

## 4. 仓库结构

```
skills/
├── README.md
├── AGENTS.md                       # 去个人化约定 + "优先用 gzh，勿现想 bash"
├── gzh/                            # ★ Python CLI 包（确定性工具）
│   ├── pyproject.toml              # 入口点 gzh；依赖 portage(系统自带)、httpx、click
│   ├── gzh/
│   │   ├── cli.py                  # 统一入口 gzh <subcommand>
│   │   ├── repo.py                 # 定位 overlay 开发副本、解析 cat/PN 路径
│   │   ├── ebuild_parser.py        # 用 portage 库解析 ebuild 变量
│   │   ├── lint.py                 # devmanual 规则 + gentoo-zh ~arch 约束
│   │   ├── upstream.py             # VersionProvider 接口 + NvcheckerProvider
│   │   ├── manifest.py             # pkgdev manifest 封装
│   │   ├── pkgcheck.py             # pkgcheck scan 结构化输出（按严重度过滤）
│   │   ├── buildtest.py            # 分级编译验证
│   │   ├── bump.py                 # bump-scaffold / diff-ebuild
│   │   └── commit.py               # pkgdev commit 封装
│   └── tests/                      # pytest L1
├── .agents/skills/                 # skill 定义（opencode + claude 兼容路径）
│   └── gzh-version-bump/               # ★ MVP 场景子 skill
│       ├── SKILL.md                # 含内嵌"收尾"小节
│       └── references/
│           ├── upstream-lookup.md  # 各上游源(pypi/github/git/apt)取版本策略
│           └── finish-pipeline.md  # 收尾 6 步 gzh 调用 + 硬/软门说明
├── docs/
│   └── devmanual.md                # 共享权威参考：devmanual 章节索引（带链接）
└── tests/
    └── e2e/                        # L3 端到端冒烟（真实小包）
```

**设计要点：**
- **执行器不作为独立 skill**：opencode skill `name` 必须匹配 `^[a-z0-9]+(-[a-z0-9]+)**$`（不含下划线），且收尾流程仅 6 步 `gzh` 调用——不值得为此建独立 skill / 间接层。收尾流程内嵌每个场景子 skill 的 `finish-pipeline.md`。
- **skill 放 `.agents/skills/`**：opencode 与 claude 均兼容该路径。用户 clone 后 symlink 到 `~/.agents/skills/`（或 `~/.config/opencode/skills/`）即可被发现。
- **devmanual 规范**放共享 `docs/devmanual.md`，所有 skill 引用同一份，避免抄进 skill 文档后失同步。

---

## 5. `gzh` Python CLI 工具

可 `pip install -e ./gzh` 安装，agent 统一调 `gzh <子命令>`，每条命令返回结构化 JSON。

### MVP 子命令（gzh-version-bump 所需）

| 子命令 | 用途 | 底层 |
|---|---|---|
| `gzh repo` | 定位 overlay 开发副本根、解析 cat/PN | git toplevel + `$GZH_OVERLAY_DIR` |
| `gzh ebuild-parse <ebuild>` | 解析 ebuild 变量（PV/EAPI/SRC_URI/KEYWORDS/DEPEND…） | portage 库 |
| `gzh lint <ebuild>` | devmanual 规则 + gentoo-zh 约束检查 | 见 §8 |
| `gzh upstream-version <cat/pkg>` | 查单个包上游最新版 | VersionProvider（默认 nvchecker，见 §7） |
| `gzh bump-scaffold <cat/pkg> <newver>` | 复制最高旧 ebuild 为新版本文件 | 文件操作 |
| `gzh nvchecker-config <cat/pkg> <get\|set>` | 读/写 overlay.toml 的 source 配置 | toml 读写 |
| `gzh manifest <ebuild>` | 重新生成 Manifest | `pkgdev manifest` |
| `gzh pkgcheck [--min-severity error] <path>` | QA 扫描，结构化输出 | `pkgcheck scan` |
| `gzh build-test <ebuild> --level quick\|full\|none` | 分级编译验证 | `ebuild <phase>` 序列 |
| `gzh diff-ebuild <old> <new>` | 新旧 ebuild 差异 | difflib |
| `gzh commit` | 规范 commit message | `pkgdev commit` |

### 后续阶段子命令（不进 MVP）

- `gzh outdated [--owner <name>] [--pkg <cat/pkg>]`：列出过期包（阶段 2，nvcmp）
- `gzh drop-old <cat/pkg>`：按规则 drop 最老版本（阶段 2 定时任务）

**依赖策略**：`portage`（Gentoo 系统自带）必用；HTTP 用 `httpx`；CLI 用 `click`。`gzh` 需运行在 Gentoo 系统（有 portage/pkgdev/pkgcheck/ebuild）。

---

## 6. 版本检测层

gentoo-zh 已有完整上游检测基础设施（**默认复用，不重复造轮子，但可替换**）：

- `.github/workflows/overlay.toml`（1886 行，全部包的 nvchecker source 配置，含 pypi/github/apt/git 等）
- CI（`.github/workflows/nvchecker.yml`）：`eix` 提取当前版本 → `nvchecker` 查上游 → `nvcmp --newer` 列出过期包 → `bumpbot`（`gentoo-zh-drafts/bumpbot`）开 GitHub issue。
- **缺口**：开 issue 后，bump 动作仍纯人工 ← 本 skill 要自动化的落点。

**`gzh` 内部 `VersionProvider` 接口 + 默认 `NvcheckerProvider`：**
- 读 `overlay.toml`，调 `nvchecker`/`nvcmp`。
- **无配置**回退内置策略（pypi/github API），并提示"建议补 overlay.toml 配置"。
- provider 接口让"换掉 nvchecker"成为局部改动，不牵一发动全身。

执行器阶段 0（准备）含"用 `gzh upstream-version` 确认目标上游版本"。阶段 2 扫描发现层 = 读 bumpbot issue **或** 本地 `gzh outdated`（全量慢，建议走 CI；`--pkg` 单包本地用）。

---

## 7. 执行器 pipeline（所有场景共用的收尾流程）

场景子 skill 完成阶段 A（特化改动）后，按 `finish-pipeline.md` 走收尾。阶段以 devmanual 为权威。

```
[0 准备] → [A 场景特化改动] → [1 校验] → [2 Manifest] → [3 QA] → [4 编译验证] → [5 变更摘要] → [6 提交] → [7 交付]
 执行器        子 skill             └────────────── 执行器（收尾）──────────────┘
```

| 阶段 | 动作 | 工具 | 门控 | 失败处理 |
|---|---|---|---|---|
| **0 准备** | 定位包、解析当前 ebuild、确认目标上游版本、识别上游类型 | `gzh repo/ebuild-parse/upstream-version` | — | 包不存在→中止 |
| **A 特化改动** | 由场景子 skill 负责 | 子 skill | — | — |
| **1 改动校验** | ebuild 语法、EAPI、变量、phase function 顺序、`default_*` 用法、USE 条件、eclass、metadata/patches、common-mistakes、KEYWORDS 仅 ~arch | `gzh lint` | 🔴硬门 | 报告问题并停 |
| **2 Manifest** | fetch distfiles + 重算 checksums | `gzh manifest` | 🔴硬门 | fetch 失败→检查 SRC_URI，停 |
| **3 QA 扫描** | pkgcheck scan | `gzh pkgcheck --min-severity error` | 🔴硬门(Error) | Error 清零；Warning 记录不阻断 |
| **4 编译验证** | 分级编译测试 | `gzh build-test --level quick\|full\|none` | 🟡软门 | 见 §9 |
| **5 变更摘要** | 新旧 ebuild diff，agent 总结 | `gzh diff-ebuild` | — | — |
| **6 提交** | `pkgdev commit` 规范 message | `gzh commit` | — | — |
| **7 交付** | 停在安全边界 | — | — | 见 §10 |

**门控两级**：🔴**硬门**（校验/Manifest/QA-Error）必须过，过不了即停并报告，绝不带病提交；🟡**软门**（编译验证）耗时可降级，但**必须显式标记跳过原因**，不能静默。

**每步结果结构化（JSON）**，阶段间以数据衔接，失败可从上下文恢复，agent 不靠"记着"中间状态。

---

## 8. devmanual 对齐（`gzh lint` 校验项映射）

执行器"对错标准"锚定 Gentoo 官方 devmanual。`gzh lint` 把成文规范代码化（可单测）：

| 校验项 | devmanual 依据 |
|---|---|
| Ebuild file format | `ebuild-writing/file-format` |
| EAPI 合法且被 portage 支持 | `ebuild-writing/eapi` |
| 变量规范（SRC_URI/KEYWORDS/LICENSE/RESTRICT…） | `ebuild-writing/variables` |
| Phase function 顺序、`default_*`/`default` 用法 | `ebuild-writing/functions` |
| USE 条件代码规范 | `ebuild-writing/use-conditional-code` |
| eclass 继承正确 | `ebuild-writing/using-eclasses` |
| metadata.xml/patches/files 规范 | `misc-files` |
| 规避常见错误 | `common-mistakes` |
| （gentoo-zh 附加）KEYWORDS **仅 ~arch**、无 stable | gentoo-zh README 铁律 |

`common-mistakes` 单独成检查项集（高频踩坑点）。所有 skill 涉及 ebuild 写法时指向 `docs/devmanual.md`，不抄规范进 skill 文档。

---

## 9. 验证策略（阶段 4 编译验证，🟡软门细化）

| 级别 | 跑什么 | 能抓什么 | 何时用 |
|---|---|---|---|
| **none** | 不编译（仅靠阶段 3 pkgcheck 硬门） | — | 包巨大/需特殊环境(GUI/硬件)；**必须显式声明跳过原因**写入交付报告 |
| **quick**（默认） | `ebuild <f> clean unpack prepare configure` | patch 不适用、依赖缺失、SRC_URI 结构错、解包失败 | MVP 默认 |
| **full** | quick + `compile install`（可选 `FEATURES=test` 跑 `src_test`） | 真编译/链接/安装错误 | 改动风险高（动 patch/依赖/EAPI）时 agent 主动升级 |

- **默认 quick** 的理由：gzh-version-bump 最高频错误（patch 不适用、SRC_URI 变）在 `unpack/prepare` 即暴露，不必真编译。
- **编译失败 → 场景切换**：quick/full 失败时，执行器识别并提示"建议转 `fix-build-failure` 场景"（该场景阶段 1 才实现）。
- **bin 包**：full 也快（解压二进制），建议 bin 包默认 full。
- **编译环境**：MVP 在用户主机直接跑 `ebuild`，不建 chroot/container；容器作为阶段 3 可选 backend。
- 按 devmanual phase 顺序执行（`pkg_setup→src_unpack→src_prepare→src_configure→src_compile→src_install`，`src_test` 由 `FEATURES=test` 控制）。

---

## 10. 交付、安全边界与去个人化

**交付终点（阶段 6→7）：**
- **默认产物**：开发副本 feature 分支上的 `pkgdev commit` + diff 摘要报告。**停在此，不自动 push。**
- **可选 `--pr`**：显式开启才开 PR；PR body 可链接/关闭 issue。
- **绝不直接动 `master`**，绝不自动 push 覆盖他人分支。

**🟢 去个人化（组织级仓库硬原则）：**

| 维度 | 做法 |
|---|---|
| 开发副本路径 | `gzh repo` 自动发现 git toplevel；`$GZH_OVERLAY_DIR` 覆盖。不假设个人路径 |
| Fork / PR head | 动态取 `gh api user`，不写死 `liangyongxiang` |
| Maintainer | 从现有 ebuild 的 metadata.xml 继承或参数传入 |
| nvchecker owner | `gzh outdated --owner <name>` 参数化 |
| 测试环境 | 不假设 incus；quick 默认主机跑，容器为可选 backend（阶段 3） |

**安全边界（不能做）：**
- 不动 `master`、不自动 push/PR（除非 `--pr`）。
- 不写 `/var/db/repos/gentoo-zh`（synced 副本，会被 `emaint sync` 覆盖）——只在开发副本工作。
- 不改系统配置（`make.conf` 等）。
- commit **无任何 AI 署名**（`Co-Authored-By`/`🤖 Generated` 一律不写）。

---

## 11. 错误处理与可恢复性

- 每个 task 在独立 feature 分支（`category-package-${VERSION}`，从 `origin/master` 切），失败不污染其他。
- 🔴硬门失败 → 中止、保留改动供诊断、输出结构化失败报告（JSON）。
- 编译失败 → 识别并提示转 `fix-build-failure` 场景。
- **重试上限 3 次**：同一错误重复 2 次即停，防死循环；停后报告失败步骤、错误、每次尝试，问用户是继续/跳过/放弃。
- 可重入：`gzh` 记录 task 状态，中断可恢复。
- Git hygiene：CI 失败修复 squash 回原 commit（`git reset --soft origin/master` + 重新提交 + force push），不在 PR 上堆 fix commit。

---

## 12. gzh-version-bump 子 skill（MVP 场景，阶段 A 特化逻辑）

只负责执行器阶段 A，产出"改好的 ebuild（git 工作区）"后移交收尾流程。

| 子步骤 | 动作 | 谁做 | 工具 |
|---|---|---|---|
| **A1 确认版本** | 上游最新 vs 当前最高 ebuild | agent | `gzh upstream-version/ebuild-parse` |
| **A2 版本号规范化** | tag `v1.2.3`→PV `1.2.3`；`rc/beta/pre`→`_rc/_beta/_pre` | agent（查 devmanual 版本规则） | — |
| **A3 脚手架** | 复制最高旧 ebuild 为新版本文件 | 脚本 | `gzh bump-scaffold` |
| **A4 SRC_URI** | 新版本归档 URL 是否仍有效/结构变化 | agent（bin 包重点） | 收尾阶段 2 Manifest 暴露 fetch 失败 |
| **A5 依赖评估** | 对照上游新版本依赖变化 | agent（源码包重点） | 上游发布说明 |
| **A6 patch 评估** | `files/` 下旧 patch 是否仍适用 | agent ★最易出错 | `gzh` 列 patch + 尝试 apply |
| **A7 旧版本处理** | **默认只 add 不 drop** | — | — |
| **A8 nvchecker 配置** | 若 overlay.toml 无该包配置，顺手补 | agent | `gzh nvchecker-config set` |

**关键决策（用户确认）：**
- **A1 不过滤预发布**：上游发布即可 bump（agent 信任上游最新）。版本号仍按 Gentoo 规范化。
- **A7 默认只 add 不 drop**：`drop` 拆为独立能力——① 定时任务（阶段 2 `gzh drop-old`）② 手动触发。MVP 不 drop。
- **A6 patch 评估**是 gzh-version-bump 最易错点，SKILL.md 重点指导：读 patch、对照新源码判断适用性，失败则重新生成。
- **区分 `-bin` 包 vs 源码包**：bin 包 gzh-version-bump 重在 SRC_URI（A4）；源码包重在依赖/patch（A5/A6）。
- **重试上限 3 次**适用于整个 A→收尾流程。

---

## 13. 测试策略

| 层 | 测什么 | 怎么测 | 回应教训 |
|---|---|---|---|
| **L1 `gzh` 单测**（重点） | 各子命令：upstream 查询、pkgcheck 解析、ebuild 解析、版本比较、overlay.toml 读写、lint 规则 | pytest + fixtures（样本 ebuild/toml/pkgcheck 输出）；网络全 mock（httpx mock 上游/GitHub API）；portage 用系统库或 mock；CI 跑 | bash 不可靠→Python 可单测 |
| **L2 skill 触发**（降级） | skill description 触发准不准 | opencode **无原生 skill eval**（frontmatter 不认 eval.yaml/eval-triggers.json；skill-creator 的 run_eval.py 绑定 claude）。MVP 靠 ① description 遵循 opencode 规则（≤1024 字、含触发词 `ebuild/gentoo-zh/bump/pkgdev`）② L3 端到端冒烟自然检验触发。量化触发测试后置（基于 run_eval.py 适配 opencode CLI，不进 MVP） | skill 太大执行不准 |
| **L3 端到端冒烟** | gzh-version-bump 全流程真实跑通 | 选 1-2 个真实小包（1 个 `-bin` + 1 个源码）作回归基线，CI cron 或手动 | 整体可用性 |

---

## 14. opencode 平台约束（影响实现）

- **skill 发现路径**：`.opencode/skills/`、`~/.config/opencode/skills/`、`.claude/skills/`、`.agents/skills/`（项目从 cwd 往上走到 git worktree + 全局）。本仓库用 `.agents/skills/`（opencode+claude 双兼容）。
- **frontmatter 只认** `name`(必)/`description`(必，≤1024 字)/`license`/`compatibility`/`metadata`。未知字段忽略。
- **name 规则**：`^[a-z0-9]+(-[a-z0-9]+)*$`，1–64 字符，必须与目录名一致。→ 故无 `_executor` skill。
- **skill 通过 `skill` 工具按需加载**，`available_skills` 列 name+description。
- **权限**：可在 `opencode.json` 用 pattern 控制（`allow/deny/ask`）。

---

## 15. MVP 交付边界与验收标准

**MVP = 执行器核心（`gzh` + 收尾流程）+ `gzh-version-bump` 子 skill。**

**包含：**
- `gzh` 包 + MVP 子命令（§5）+ L1 pytest
- `gzh-version-bump` skill（SKILL.md + upstream-lookup.md + finish-pipeline.md）
- `docs/devmanual.md` 共享参考
- `AGENTS.md`（去个人化约定 + 优先用 gzh）

**不包含（后续阶段）：** 其他场景子 skill（fix/qa/eapi/create）；扫描发现层；自动值守/`--pr` 自动化；容器测试 backend；`drop-old`。

**验收标准：**
1. 对 1 个真实 `-bin` 包 + 1 个真实源码包，跑通全流程：`upstream-version → bump-scaffold → (agent 改) → lint → manifest → pkgcheck → build-test → diff → commit`（源码包用 `quick`，`-bin` 包用 `full`，见 §9），所有🔴硬门通过，产出规范 commit（无 AI 署名），停在本地分支。
2. `gzh` L1 单测覆盖各子命令。
3. **去个人化**：任何维护者 clone 仓库 → `pip install -e ./gzh` → 设 `$GZH_OVERLAY_DIR` 即可用，无 `liangyongxiang` 硬编码。

---

## 16. 路线图

| 阶段 | 内容 |
|---|---|
| **0 / MVP** | `gzh` 核心 + 收尾流程 + `gzh-version-bump` skill |
| **1** | `fix-build-failure` / `qa-fix` / `eapi-deps` / `create` 子 skill（复用执行器） |
| **2** | 扫描发现层：`gzh outdated`、读 bumpbot issue、`drop-old` 定时任务 |
| **3** | 自动值守 + `--pr` 自动化 + 容器测试 backend（incus 可选） |

---

## 17. 附录：devmanual 章节索引（`docs/devmanual.md` 内容大纲）

权威参考，所有 skill 引用此文件（带链接，不抄内容）：

- `ebuild-writing/file-format` — Ebuild 文件格式
- `ebuild-writing/eapi` — EAPI 使用与说明
- `ebuild-writing/variables` — 变量（预定义只读 / ebuild 定义）
- `ebuild-writing/functions` — Phase functions（调用顺序 `pkg_pretend→pkg_setup→src_unpack→src_prepare→src_configure→src_compile→src_test→src_install→pkg_preinst→pkg_postinst`，`default_*` 约定）
- `ebuild-writing/use-conditional-code` — USE 条件代码
- `ebuild-writing/using-eclasses` — eclass 使用
- `ebuild-writing/error-handling` — 错误处理
- `ebuild-writing/common-mistakes` — 常见错误
- `misc-files` — metadata.xml / patches / files
- 在线根：https://devmanual.gentoo.org/ebuild-writing/index.html
