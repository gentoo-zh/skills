# escalate 类别（不可离线机械 bump 的确定性信号）

`autobump.sh` 是真值源：下面的信号逐条对应它 stage-2 的确定性分类。命中任一即 **escalate**——出证据、升级到人工判断或去取上游数据，**不 abort、也不盲改**（对应引擎 `exit 3`；区别于 `exit 2` 那种 fetch/网络 transient defer）。

两个 bump skill 共用本表，确保用**同一组具体信号**决定要不要升级：gzh-version-bump 的 A0 分类闸、gzh-bump-from-issues 阶段 2。escalate ≠ 放弃，是把「离线照抄旧 ebuild + 重算 Manifest」搞不定的那部分交给人/数据。

## autobump.sh stage-2 确定性信号

1. **预发布 vs release-only 历史**
   目标版本含 `alpha`/`beta`/`rc[0-9]*`/`pre`/`nightly`/`dev`（以 `._-` 或结尾为边界），而 PKGDIR 现有 ebuild 里**没有**任何 `_alpha`/`_beta`/`_rc`/`_pre` 版本。
   与 SKILL.md A1 对齐：目标是预发布**仍可 bump**（A1「预发布处理」不过滤上游发布流）。此信号只在**历史只有正式版**却突然要上预发布时触发——是提请人工确认「跨界到预发布」这一步，**不等于跳过预发布**。

2. **major 跨度**
   首个版本号分量变化（dae 1→2、mkinitcpio 39→41、tsukimi 0.21→26.7）算大跳。
   **例外**：`YYYYMMDD[.N]` 日期版号（`^20[0-9]{6}([._-][0-9]+)*$`）首段本就每次 bump 递增，**更新到更晚日期算常规 bump，不 escalate**；只有日期**倒退**才可疑。

3. **pin / 版本耦合变量**
   ebuild 含 `GIT_CRATES` / `*_COMMIT=` / `*_TAG=` / `[A-Z_]+_VER=`（非注释行）。这些没有可自动展开的产物，须逐一**对上游新版本 diff 核定**（重新确认 commit / tag / crates 版本），绝不照抄旧值。

4. **per-version 外部依赖产物**
   SRC_URI 里的 URL 文件名命中 `-deps` / `-vendor` / `-crates` / `node_modules` 且带 `.tar.`——**按文件名识别，不认 host**（这类产物散落在多个 deps 仓库，host allowlist 会漏，如 v2rayA 的 `${P}-deps.tar.xz`）。
   bump 前把 URL 里的 `${P}` / `${PV}` / `${PN}` 展开到新版本、并把硬编码旧版号一并替换，再 `curl -sIL --max-time 30 -o /dev/null -w '%{http_code}'` 确认新版产物已发布：
   - `200` → 存在，放行；
   - **仅确定性 `404`** → 上游依赖包还没打 → escalate；
   - 网络 `5xx`/`000` → 不下终局结论（inconclusive，留给收尾 `gzh manifest` fetch 阶段复检，属 transient）。

5. **实际应用的 patch**
   `PKGDIR/files/*.patch` 存在**且** ebuild 真的引用它（`eapply` / `epatch` / `PATCHES+=` / `FILESDIR.*\.patch`）→ 新版须验证 patch 仍适用。
   未被任何 `eapply`/`epatch`/`PATCHES`/`FILESDIR` 引用的**死 patch** 是残留，**不 escalate**（如 archlinux-keyring 里没被引用的 `01_adapt_to_sequoia`）。

## 信息类（不 escalate，仅标注 / 交付说明）

信息类信号：这些**不阻断**、也不是 escalate，只在交付里注明：

- **多 arch**：KEYWORDS 有 >1 个 `~arch` → PR 走 draft，注明非 amd64 arch 未测。
- **GUI 应用**：`inherit` 含 `desktop`/`xdg` → smoke 只能证「**装上了**」，证不了能启动/渲染；交付说明里提请人工实跑。

## 超出 autobump.sh 的补充（来源：replay-eval）

下面两条**不在** autobump.sh stage-2 里，一并纳入 escalate：

- **生成器驱动的 metadata（编辑数据非离线可复现）**：ebuild 的 DEPEND/RDEPEND/IUSE 由**上游生成器产物**决定，而非 ebuild 自身可推。典型是 `.cabal` 经 hackport 重生成（`inherit haskell-cabal`、`CABAL_HACKAGE_REVISION`，依赖边界 / ghc 版本 / flag 改名都随上游 `.cabal` 变）。这类 bump 的正确结果要从**上游 metadata 重新生成**，离线照抄旧 ebuild 几乎必错（replay-eval 里 edit-bump exact% = 0，瓶颈是缺 **DATA** 不是缺经验）→ escalate 到取上游 metadata 或人工。
  overlay 里 Haskell 包极少，但同理适用于**任何「依赖/IUSE 随生成器产物变」的包**。
  （`CABAL_HACKAGE_REVISION` 会额外把 `-revN.cabal` 作为 DIST 拉取。）

- **live-only 包**：包目录下只有 `9999` ebuild、没有发布版。因为 live ebuild 用 `EGIT_REPO_URI` 取代 `SRC_URI`、也没有 `KEYWORDS`，所以没有可离线照抄的底稿，`gzh bump-scaffold` 会直接报错 → escalate。overlay 里 74 个 live ebuild 有 20 个与发布版同目录，这些照常用发布版当底稿，不受影响。
