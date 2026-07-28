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
2. **A2 版本号规范化**（查 devmanual 版本规则）：tag `v1.2.3`→PV `1.2.3`；`rc/beta/pre`→`_rc/_beta/_pre`。上游 tag 不合 Gentoo 版本规则时，用 `MY_PV` 之类的变量保留原字面量，不要改写上游发布名。tracker 报的版本只是线索，真正的 tag、产物、URL 要各自核实。`-rN` 是 Gentoo 修订号，**绝不**拿 `${PVR}`/`${PF}` 去拼上游 tag 或文件名。
3. **A3 脚手架**：`git fetch <canonical-remote> && git checkout -b <cat>-<pn>-<new_pv> <canonical-remote>/master`（canonical = 指向 gentoo-zh/overlay 的 remote：fork clone 为 upstream、direct clone 为 origin；勿从个人 fork 的 master 切，见 AGENTS.md），然后 `gzh bump-scaffold <cat/pkg> <new_pv>`。
4. **A4 SRC_URI**：检查新版本归档 URL 是否仍有效（`-bin` 包重点）。fetch 错误由收尾阶段 `gzh manifest` 暴露。
   - **每 arch 逐块核 URL**：`SRC_URI` 分 `<arch>? ( ... )` 时逐块取新版本的真实 URL。URL 路径含 CDN 哈希、build id、release id 这类与 `${PV}` 无关的段时，复制旧 ebuild 再 sed 版本号必然错，必须回上游下载页逐 arch 取；amd64 改对了不代表 arm64/loong 跟着对。
   - **静默装成旧版本**：`SRC_URI` 把版本号写死（不是 `${PV}`）时忘了改，URL 还指向旧文件，而 `-> ${P}_<arch>.deb` 这类重命名会把它按新名字存下来，manifest 与 install 全过、装的却是旧版本。因为同一份文件哈希相同，所以收尾 `gzh manifest` 之后拿每个 arch 的新 DIST 哈希与上一版同 arch 比一次，相同就回去核 URL。
   - 某个 arch 的新版产物确实还没发布时停下报告：旧版本按 A7 留住，新 ebuild 不 keyword 也不引用没发布的产物。
   - **per-version 依赖产物存在性**：若 ebuild 引用 per-version 外部依赖产物（`SRC_URI` 文件名命中 `-deps`/`-vendor`/`-crates`/`node_modules.tar.*`，按文件名识别、不认 host——此类包散落在多个 deps 仓库）：把 URL 里的 `${P}`/`${PV}`/`${PN}` 展开到新版本、并把硬编码旧版号一并替换后，`curl -sL -r 0-1 -o /dev/null -w '%{http_code}' --max-time 30 <url>` 确认新版产物已发布，200/206 才算存在。因为 HEAD 不走真正的取回路径、有的 host 对 HEAD 回 403/405 或直接给缓存结果，所以以带 range 的 GET 为准。仅确定性 404 判上游依赖包还没打，停止并报告；5xx/000 不下终局结论（留给收尾 `gzh manifest` fetch 阶段复检）；403 先用 emerge 实际用的 fetcher 复跑一次（`wget -U 'Portage (Gentoo, https://www.gentoo.org) distfile-fetch' -O /dev/null <url>`），两种取法都失败才判不可 fetch。
   - **pin 变量核定**：若 ebuild 含 `GIT_CRATES`/`*_COMMIT`/`*_TAG`/`*_VER` 等 pin 变量，它们没有可 curl 的产物，须逐一对照上游新版本重新核定（commit/tag/crates 版本），停止并报告待人工确认——这与上面的产物存在性检查是两条独立路径。
5. **A5 依赖评估**（源码包重点）：对照上游新版本的依赖变化，最小改动 `DEPEND/RDEPEND`。出处按生态取：c/c++ 读 `meson.build`/`CMakeLists.txt` 里的 `dependency()`/`find_package()`，python 读 sdist 的 `PKG-INFO`（没有就读构建出的 wheel 里 `METADATA`）的 `Requires-Dist`，其中带 `extra == ` 标记的是可选 extra、不进无条件 `RDEPEND`。指不出出处的依赖保持原样并写进交付报告，不换成看起来更合理的值。
   - **package.mask 闸**：新增或保留的依赖、以及 any-of 里列的备选 provider，在本 overlay 的 `profiles/package.mask` 和主树的（路径用 `portageq get_repo_path / gentoo` 取，不写死）各查一遍待删条目。因为命中的包已经排期移除，所以现在加进去等于埋一个到期就坏的依赖，要换 provider。目标包自身命中时停下问维护者，不给待删的包做 bump。
6. **A6 patch 评估**（★最易出错）：读 `files/` 下旧 patch，对照新版本源码判断是否仍适用。
   - 动手前先 `grep -l <patch 文件名> <pkgdir>/*.ebuild`。因为只要还有别的 ebuild（含 `-9999`）引用它，原地改写或删除就会让那个版本 `eapply` 当场失败，所以此时只能另加版本特定命名的新文件 `${PN}-<适用版本>-<用途>.patch`，各 ebuild 的 `PATCHES` 各指各的；等最后一个引用它的 ebuild 被 drop，在同一个 commit 里删掉旧文件。只有该 patch 仅被本次要替换的那个 ebuild 引用时，才可以就地重生成或删除。
   - 新写或重生成的 patch 在文件头记来源（上游 commit/PR/advisory URL 或 bug 链接）与适用版本。
   - 顺带盘点 ebuild 自身携带的旧规避，逐条判去留、不默认照抄：上界原子 `<cat/pkg-X`、`append-flags`/`filter-flags` 里的 `-std=`、`filter-lto`、`emake -j1`、`RESTRICT=test`、`EPYTEST_DESELECT`/`CMAKE_SKIP_TESTS` 的每个条目、带 TODO 或 bug URL 的行。因为这些规避不会自己报错、照抄永远跑得通，所以判据是上游新版修没修，不是它还能不能跑。删掉哪条在交付报告各写一行。
   - 改 patch 后会触发收尾 `build-test` 的 patch test。
7. **A7 旧版本处理**：照该包**自己的历史模式**做，不看单条最近 commit——overlay 里三种模式都有：滚最新（`add X, drop Y`）、全留、留一个锚点其余滚。模式混乱、与当前树冲突、或该包没有 bump 历史时，**默认只 add 不 drop** 并把这个选择报给维护者。major 分量跨跃、大改写、构建系统迁移一律只 add（`-bin` 包也一样），旧版本是新版跑通前的退路。真要 drop 时，先在 overlay 和主树搜反向依赖的版本 pin，按 SLOT 确认留下的版本在每个保留 arch 上都解析得开。
8. **A8 nvchecker 配置**：若 `gzh upstream-version` 返回 `advisory`（无 overlay.toml 条目），用 `gzh nvchecker-config set <cat/pkg> --json '...'` 补上，并提示人工 review（注释会丢失）。
9. **A9 live 孪生同步**：`ls <pkgdir>/*-9999.ebuild`，没有就跳过。有 live 孪生时，本次 bump 里与版本无关的改动——依赖增删、QA 变量、EAPI、phase 函数——在同一个 commit 里一并改到 live；`SRC_URI`、`MY_PV`、pin 变量、per-version 依赖产物、`Manifest` 只跟版本走，不搬。patch 要单独判断能否套上 live 跟的 master 源码，套不上或上游已合并就不搬。因为 live 与发布版共用同一套依赖和 phase 逻辑，所以漏搬会让 live 一直带着已经修好的毛病。

**区分包类型**：`-bin` 包重在 A4（SRC_URI）；源码包重在 A5/A6（依赖/patch）。按包生态的专项检查清单见 [ecosystem-checks.md](references/ecosystem-checks.md)（A5/A6 对照、build-test 红时倒查）。

## 收尾
阶段 A 完成后，**无条件**进入 [finish-pipeline.md](references/finish-pipeline.md)（校验→Manifest→QA→build-test→diff→commit）。重试上限 3 次。

## 排除
- 新建包（用 create，未实现）。
- 修复编译失败（若 build-test 失败，转 fix-build-failure，未实现）。
