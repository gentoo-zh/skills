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
