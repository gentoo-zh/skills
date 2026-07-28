# Agent 约定

overlay 自身的 `AGENTS.md` 是硬标准，本文件与它冲突时以它为准；这里只写 gzh 工具链落地时会改变做法的部分。

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

## 提交与 PR 文本
- commit subject 用 `pkgdev` 生成的英文原文，不翻译不改写；bump 是 `category/package: add NEW`，只有真的删旧版才加 `, drop OLD`。一行不折行，≤69 字元（GLEP 66）。
- subject 讲得完就不写 body。要写 body 时只写理由，用因为…所以…的句式把因果讲明；不复述 diff、不复述标题里已有的包名和版本、不报告通过的构建或扫描。
- 每个改动的依赖、phase 函数、patch、USE flag、`RESTRICT`、revbump 各占一行。
- `gzh commit` 封装的是 `pkgdev commit --scan false --signoff --gpg-sign`（环境无 GPG key 时自动省略 `--gpg-sign`）：`--scan false` 是因为 `gzh pkgcheck` 已是独立硬门，不重复跑。绝不用裸 `git commit`。
- PR 标题就是 `pkgdev` 生成的那条英文 subject 原文，不翻译不改写。
- PR body 与 commit body 同一套理由，只用一种语言（跟提出需求的人走），不双语并排。例行或行为不变的改动，body 只需 `Closes #N`。
- PR body 不写通过的测试，也不写测了哪些 arch——勾选框和 CI 已经证明；只有测试逼出了改动才提一句。
- overlay 的 issue 在 PR body 里保持裸写 `Closes #N`；不要传给 `pkgdev commit -b/--bug` 或 `-c/--closes`（那两个参数的裸数字指 Gentoo Bugzilla ID），也不要改写成 Bugzilla URL。
- 保留 PR 模板：描述写在 marker 上方，勾选框原样留着，只勾真的跑过的检查。
- 开 PR 或改 PR（`gh pr create`/`gh pr edit`）之前，把确切的标题、正文、文件清单给人看并取得**这一个 PR** 的确认；批量放行不算逐个确认，draft 也一样。

## 交付报告
每次改动完成后报告：topic 分支、canonical remote 与 fetch 状态、base 与同步状态、改了哪些文件、跑了哪些命令及其通过/失败、跳过的检查及原因、剩余的警告与风险。

## 与 CI 门对齐（验收权威）
`gzh` 是 bump 契约的一个实现；**权威验收门是 overlay 自身的 CI**：
- `.github/workflows/pkgcheck.yml`：**离线** pkgcheck（`pkgcore/pkgcheck-action`，args 无 `--net`，`--exit=NonexistentDeps`），只做元数据/依赖类检查，**不做联网 URL 复查**。
- `.github/workflows/emerge-on-pr.yml`：每个包在 `amd64-desktop-openrc`、`amd64-desktop-systemd` 两个 profile 上各 emerge 一次；`KEYWORDS` 含 `arm64` 或 `~arm64` 的包，因为 arm64 没有 desktop-openrc 的 stage3，所以另在 `ubuntu-24.04-arm` runner 上补跑 `arm64-desktop-systemd` 一条腿。每条腿都判 **elog 硬门**：`PORTAGE_ELOG_CLASSES="qa warn error"`、`PORTAGE_ELOG_SYSTEM="save"`，post-emerge 步骤扫 `/var/log/portage/elog/*` **文件**（非 stdout），任一 qa/warn/error elog 即 `exit 1`。上游支持或发布了 arm64 就加 `~arm64`，不要因为手上没有 arm 机器而不加：CI 那条腿会真编、elog 门照判，出了 arch 相关的问题按报告修。手上有 arm 设备的一定要自己先验过再加。

`gzh` 尚未覆盖的门，收尾阶段须手动补跑：
- **install 后 elog 检查**：`buildtest.py` 只看 returncode，QA notice 走 elog 不进 stdout，本地全绿仍可能踩 CI elog 硬门 → 用 CI 同款配置（`PORTAGE_ELOG_CLASSES="qa warn error"` + 隔离 LOGDIR）本地复检 elog 文件。
- **联网 DeadUrl 复查**：`pkgcheck scan --commits --net`（查 DeadUrl/RedirectedUrl）——CI 与 `gzh pkgcheck` **都不跑**，属 PR 勾选项，须手动补跑。

勿因本地 `gzh` 全绿即认为 CI 会绿。良性 elog（Unresolved soname/CONFIG_CHECK/binchecks）注明放行，真问题改 ebuild。
