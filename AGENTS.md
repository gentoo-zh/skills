# Agent 约定

## 工具优先级
gentoo-zh 维护**优先使用 `gzh` 工具集**（确定性、可单测），不要现想 bash 命令拼凑。
安装: `pip install -e ./gzh`。

## 去个人化（硬原则）
- overlay 根用 `gzh repo`（git toplevel 或 `$GZH_OVERLAY_DIR`），不硬编码任何个人路径。
- PR head 动态取 `gh api user`，不写死 fork owner。
- 不写 `/var/db/repos/gentoo-zh`（synced 副本）；只在开发副本工作。

## gentoo-zh 规则
- `~arch` only，无 stable keyword。
- 一包一分支一 commit 一 PR；分支从 **canonical remote 的 master** 切（canonical = 指向 gentoo-zh/overlay 的 remote：fork clone 为 `upstream`，direct clone 为 `origin`；勿从可能过期的个人 fork master 切），命名 `category-package-${VERSION}`。
- commit 无 AI 署名（`Co-Authored-By`/`🤖 Generated` 一律不写）。
- 提交前必过 `gzh lint` + `gzh manifest` + `gzh pkgcheck`（🔴硬门）。
- 本地 `gzh` 全绿 ≠ CI 绿：验收权威是 overlay 自身的 CI，其未覆盖的门（联网 DeadUrl、install 后 elog）收尾须手动补跑，见「与 CI 门对齐」。
- ebuild 写法以 `docs/devmanual.md` 为权威。
- 重试上限 3 次：同一错误重复 2 次即停，报告并询问。

## 与 CI 门对齐（验收权威）
`gzh` 是 bump 契约的一个实现；**权威验收门是 overlay 自身的 CI**：
- `.github/workflows/pkgcheck.yml`：**离线** pkgcheck（`pkgcore/pkgcheck-action`，args 无 `--net`，`--exit=NonexistentDeps`），只做元数据/依赖类检查，**不做联网 URL 复查**。
- `.github/workflows/emerge-on-pr.yml`：多 profile（amd64-desktop-openrc/systemd）emerge + **elog 硬门**：`PORTAGE_ELOG_CLASSES="qa warn error"`、`PORTAGE_ELOG_SYSTEM="save"`，post-emerge 步骤扫 `/var/log/portage/elog/*` **文件**（非 stdout），任一 qa/warn/error elog 即 `exit 1`。

`gzh` 尚未覆盖的门，收尾阶段须手动补跑：
- **install 后 elog 检查**：`buildtest.py` 只看 returncode，QA notice 走 elog 不进 stdout，本地全绿仍可能踩 CI elog 硬门 → 用 CI 同款配置（`PORTAGE_ELOG_CLASSES="qa warn error"` + 隔离 LOGDIR）本地复检 elog 文件。
- **联网 DeadUrl 复查**：`pkgcheck scan --commits --net`（查 DeadUrl/RedirectedUrl）——CI 与 `gzh pkgcheck` **都不跑**，属 PR 勾选项，须手动补跑。

勿因本地 `gzh` 全绿即认为 CI 会绿。良性 elog（Unresolved soname/CONFIG_CHECK/binchecks）注明放行，真问题改 ebuild。
