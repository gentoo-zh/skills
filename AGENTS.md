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
- 一包一分支一 commit 一 PR，分支从 `origin/master` 切（`category-package-${VERSION}`）。
- commit 无 AI 署名（`Co-Authored-By`/`🤖 Generated` 一律不写）。
- 提交前必过 `gzh lint` + `gzh manifest` + `gzh pkgcheck`（🔴硬门）。
- ebuild 写法以 `docs/devmanual.md` 为权威。
- 重试上限 3 次：同一错误重复 2 次即停，报告并询问。
