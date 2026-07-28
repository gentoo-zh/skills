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
- **版本跨度**：`target_version` vs 当前 ebuild PV（`gzh ebuild-parse`）。major 跨跃 → 标记「需查上游 changelog」，A5 加权。例外：YYYYMMDD[.N] 日期版号首段单调递增，更新日期是常规 bump，不算 major；只有日期倒退才可疑。
- **包类型**：`-bin`/预编译（SRC_URI + 预编译 QA：unresolved-soname/pre-stripped/.desktop，见 ../gzh-version-bump/references/prebuilt-qa.md）/ 源码（依赖+patch，A5/A6 加权）。
- **不可离线复现类 (escalate)**：metadata 重生成（haskell-cabal/hackport、CABAL_HACKAGE_REVISION）或 DEPEND/RDEPEND/IUSE 须随上游生成器改变的包 → 不盲试，`gzh triage skip <issue> --cat-pkg <p> --target-version <v> --reason "需上游 metadata，非离线可复现"`，留取数据/人工路径；信号见 ../gzh-version-bump/references/escalate-classes.md。

### 阶段 3：循环处理
- **skip 的** → `gzh triage skip <issue> --cat-pkg <p> --target-version <v> --reason "<理由>"`。
- **bump 的** → 按 **gzh-version-bump** skill 的 A→收尾全流程（A3 含 `git fetch <canonical-remote> && git checkout -b <cat>-<pn>-<ver> <canonical-remote>/master`，勿重复 checkout；canonical = 指向 gentoo-zh/overlay 的 remote，见 AGENTS.md，勿从个人 fork 的 master 切），**到本地 commit 为止**：
  - A1-A8 + 收尾（`gzh lint`/`manifest`/`pkgcheck`/`build-test`/`diff-ebuild`/`commit`）。
  - 硬门失败（manifest/patch/build-test）→ **记失败**（汇总：cat/pkg + phase + error + 诊断分支），**不写 triage skip**（尝试失败 ≠ 主动跳过），build-test 失败提示「转 fix-build-failure（未实现）」。
  - 重试上限 3 次：同包同错重复 2 次即停、记失败、继续下一个。

### 阶段 4：汇总 + 回报
1. 写汇总到 `.gzh/bump-batch-<时间戳>.md`：成功（cat/pkg-ver + 分支 + issue）/ 失败（cat/pkg + phase + error + 分支）/ 跳过（cat/pkg + issue + reason，已记 triage）+「下一步：手动 PR」命令模板（`gh pr create --repo gentoo-zh/overlay --base master --head $(gh api user --jq .login):<branch>`；每个 PR 开之前都要把标题、正文、文件清单给人逐个确认，见 AGENTS.md）。
2. 若 `TELEGRAM_BOT_TOKEN` env 配置：`gzh notify telegram --message "<成功N/失败N/跳过N + 分支列表>"`；否则跳过。

## 排除
- 自动 push/PR（停本地 commit，用户手动 PR）。
- fix-build-failure（失败只记录+提示）。
