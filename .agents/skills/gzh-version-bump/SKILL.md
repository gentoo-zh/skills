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

0. **A0 分类闸（先跑，判断能否离线复现）**：读 `gzh ebuild-parse` 的 inherit 与关键变量（注意 `GIT_CRATES` 等 `declare -A` 数组 parser 不暴露，需对 ebuild 原文 grep `GIT_CRATES|_COMMIT=|_TAG=|_VER=`），命中任一即 escalate（出证据、不进盲改）：① metadata 重生成类（inherit haskell-cabal / hackport 生成、`CABAL_HACKAGE_REVISION`，依赖/IUSE 随生成器变）；② 上游数据先决（`GIT_CRATES`/`*_COMMIT`/`*_TAG`/`*_VER`、per-version 的 `-deps`/`-vendor`/`-crates` 产物、ebuild 实际 `eapply` 的 `files/` patch）；③ 大跨度（major 分量变化；或 release-only 历史却上 prerelease——见 A1「预发布处理」，此处 escalate 提请人工确认而非静默过滤）。escalate = 升级不 abort，留人工/取数据（对应 autobump.sh stage-2 exit 3）。信号清单见 [escalate-classes.md](references/escalate-classes.md)。
1. **A1 确认版本**：`gzh upstream-version <cat/pkg>` 查上游最新；`gzh ebuild-parse <最高旧 ebuild>` 读当前 PV。**预发布处理**：该包 ebuild 历史已有 `_alpha`/`_beta`/`_pre`/`_rc` 版本时，直接 bump 预发布；若历史 release-only 而上游最新是预发布（alpha/beta/rc/pre/nightly/dev），交付报告标注「target 是预发布，需人工确认」，**不静默 bump 也不硬 abort**（escalate 到人工判断、人在环；对应引擎 exit 3，不是 exit-2 的 transient defer）。若上游 ≤ 当前，停止并报告。
2. **A2 版本号规范化**（查 devmanual 版本规则）：tag `v1.2.3`→PV `1.2.3`；`rc/beta/pre`→`_rc/_beta/_pre`。
3. **A3 脚手架**：`git fetch origin && git checkout -b <cat>-<pn>-<new_pv> origin/master`，然后 `gzh bump-scaffold <cat/pkg> <new_pv>`。
4. **A4 SRC_URI**：检查新版本归档 URL 是否仍有效（`-bin` 包重点）。fetch 错误由收尾阶段 `gzh manifest` 暴露。
   - **per-version 依赖产物存在性**：若 ebuild 引用 per-version 外部依赖产物（`SRC_URI` 文件名命中 `-deps`/`-vendor`/`-crates`/`node_modules.tar.*`，按文件名识别、不认 host——此类包散落在多个 deps 仓库）：把 URL 里的 `${P}`/`${PV}`/`${PN}` 展开到新版本、并把硬编码旧版号一并替换后，`curl -sIL --max-time 30 -o /dev/null -w '%{http_code}'` 确认新版产物已发布——仅确定性 404 判「上游依赖包还没打」，停止并报告；网络 5xx/000 不下终局结论（留给收尾 `gzh manifest` fetch 阶段复检）。
   - **pin 变量核定**：若 ebuild 含 `GIT_CRATES`/`*_COMMIT`/`*_TAG`/`*_VER` 等 pin 变量，它们没有可 curl 的产物，须逐一对照上游新版本重新核定（commit/tag/crates 版本），停止并报告待人工确认——这与上面的产物存在性检查是两条独立路径。
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
