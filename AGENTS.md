# Agent 约定

overlay 自身的 `AGENTS.md` 是硬标准，本文件与它冲突时以它为准；这里只写 gzh 工具链落地时会改变做法的部分。开工第一件事是读 `$(gzh repo)/AGENTS.md` 全文；因为对话变长或上下文被裁剪后它的条文会掉出上下文，所以那时要重读一遍再往下做。

## 工具优先级
gentoo-zh 维护**优先使用 `gzh` 工具集**（确定性、可单测），不要现想 bash 命令拼凑。
安装：`pip install -e ./gzh`。

## 去个人化（硬原则）
- overlay 根用 `gzh repo`（git toplevel 或 `$GZH_OVERLAY_DIR`），不硬编码任何个人路径。
- PR head 动态取 `gh api user`，不写死 fork owner。
- 不写 `/var/db/repos/gentoo-zh`（synced 副本）；只在开发副本工作。

## gentoo-zh 规则
- `~arch` only，无 stable keyword。
- 默认一包一分支一 commit 一 PR。只有依赖链、需要联动的同步 bump、同一个修复跨包这三种情况才允许多包共用一个 PR，此时仍是一包一 commit、被依赖的包排在使用它的 commit 之前，无关的包不并进来。因为 emerge-on-pr 的 build job 检出的是 PR head 分支自身的 `head.sha`、不是与 master 的合并结果，所以把依赖链硬拆成两个 PR，依赖方那条会一直红到被依赖的包合并、且依赖方分支 rebase 重推之后才转绿。分支从 **canonical remote 的 master** 切（canonical = 指向 gentoo-zh/overlay 的 remote：fork clone 为 `upstream`，direct clone 为 `origin`；勿从可能过期的个人 fork master 切），命名 `category-package-${VERSION}`。
- commit 无 AI 署名（`Co-Authored-By`/`🤖 Generated` 一律不写）。
- 提交前必过 `gzh lint` + `gzh manifest` + `gzh qa`（🔴硬门）。
- 本地 `gzh` 检查全部通过不等于 CI 通过：验收以 overlay 自身的 CI 为准。联网 DeadUrl 复查和安装后的 elog 检查未被 CI 覆盖，必须在收尾阶段补充执行，详见下文的与 CI 门对齐一节。
- ebuild 写法以 `docs/devmanual.md` 为权威。
- 一次 bump 默认 `add NEW, drop OLD`。保留旧版是例外，仅限三种理由：大版本跳跃、大规模重写或构建系统迁移；反向依赖固定了该版本，或 `SLOT`、profile 条目仍引用它；替代版本尚未验证，例如上游这一版未发布某个架构，或安全修复尚未覆盖全部 `KEYWORDS` 分支。理由必须写进交付报告。`overlay.toml` 的 `keep_old` 同一套标准，理由消失就删掉。`gzh drop-old` 只按版本序列列候选，不判断理由也不删文件，默认保留一个发布版本加 live ebuild。
- 仅修改新版本使其失效的 ebuild 内容。新版本暴露的缺陷必须在现有 ebuild 中修复；仅当新版本使该 ebuild 无法使用时才可重写，并说明导致其无法使用的上游变更。未因新版本失效的写法保持不变，不重排、不调整风格、不翻新。
- 大多数 bump 只需重命名 ebuild。先比对两个版本的归档清单或产物集合、构建元数据与 lockfile、声明的依赖和选项以及许可证，这一次比对就是路由证据；只有某项存在差异时，才加载该项对应的参考并执行相应的专项检查。
- 同一棵树上已经通过的检查不重复执行：已经干净构建过的不再构建，已经通过的扫描不再执行。rebase 或新 commit 会让之前的 commit 扫描失效、必须重新执行，但这不构成跳过硬门的理由。
- 判断某项检查结果是否由本次改动引入时，先查阅包历史、上一版本的检查结果以及 PR 中的 pkgcheck bot 报告。早于本次改动的结果属于既有问题，必须在报告中注明，且不并入本次改动一起修复。
- 重试上限 3 次：同一错误重复 2 次即停，报告并询问。

## 提交与 PR 文本
- commit subject 用 `pkgdev` 生成的英文原文，不翻译不改写；bump 是 `category/package: add NEW`，只有真的删旧版才加 `, drop OLD`。一行不折行，≤69 字元（GLEP 66）。
- subject 讲得完就不写 body。要写 body 时只写理由，用因为…所以…的句式把因果讲明；不复述 diff、不复述标题里已有的包名和版本、不报告通过的构建或扫描。
- 每个改动的依赖、phase 函数、patch、USE flag、`RESTRICT`、revbump 各占一行。
- `gzh commit` 封装的是 `pkgdev commit --scan false --signoff --gpg-sign`：`--scan false` 是因为 `gzh qa` 已是独立硬门，不重复执行；`--gpg-sign` 只在 git 配了 `user.signingkey` 且签名程序（`gpg.program`，默认 `gpg`）在 PATH 上时才加，因为 AGENTS.md 要求 GPG 不可用时省略该参数。省略了也不等于没签名，`commit.gpgsign=true` 时 git 照签，别在报告里写成未签名。绝不用裸 `git commit`。
- PR 标题就是 `pkgdev` 生成的那条英文 subject 原文，不翻译不改写。
- PR body 与 commit body 同一套理由，只用一种语言（跟提出需求的人走），不双语并排。例行或行为不变的改动，body 只需 `Closes #N`。
- PR body 不写通过的测试，也不写测了哪些 arch——勾选框和 CI 已经证明；只有测试逼出了改动才提一句。
- overlay 的 issue 在 PR body 里保持裸写 `Closes #N`；不要传给 `pkgdev commit -b/--bug` 或 `-c/--closes`（那两个参数的裸数字指 Gentoo Bugzilla ID），也不要改写成 Bugzilla URL。
- 保留 PR 模板：描述写在 marker 上方，勾选框保持原样，只勾选实际执行的检查。
- 开 PR 或改 PR（`gh pr create`/`gh pr edit`）之前，把确切的标题、正文、文件清单给人看并取得**这一个 PR** 的确认；批量放行不算逐个确认，draft 也一样。
- 仅当例行 bump 只重命名 ebuild 并执行 `gzh manifest`，或只更新 build id 这类版本变量、其余全不变时，才可自行创建 PR，且仍须逐个确认。其他改动只推送到个人 fork，并把拟定的标题、正文以及相对 canonical remote `master` 的 compare 链接交给提出请求的人创建。链接形式为 `https://github.com/gentoo-zh/overlay/compare/master...<fork-owner>:<fork-repo>:<branch>`。
- 因为主树尚未提供抬高后的工具链下限（`>=dev-lang/go` 或 `RUST_MIN_VER`），所以同样把分支交给提出请求的人，并说明该 PR 必须以 draft 创建，同时附上上游 `go.mod` 或 `Cargo.toml` 的最低版本声明和 packages.gentoo.org 上的当前树内版本。
- PR 里有多个 commit 时，标题取承载主要改动那条 commit 的 subject。
- compare 链接和 PR 都以 canonical remote 的 `master` 为 base，不以个人 fork 的 `master` 为 base，因为个人 fork 落后会使 diff 包含无关 commit。
- 在说明某个 commit 或 PR 已合并、或据此继续后续工作之前，先重新 fetch canonical remote，因为过期的 ref 会给出相反的合并状态。

## 交付报告
每次改动完成后报告：topic 分支、canonical remote 与 fetch 状态、base 与同步状态、改了哪些文件、执行了哪些命令及其通过/失败、跳过的检查及原因、早于本次改动的既存问题、剩余的警告与风险。

## 与 CI 门对齐（验收权威）
`gzh` 是 bump 契约的一个实现；**权威验收门是 overlay 自身的 CI**：
- `.github/workflows/pkgcheck.yml`：**离线** pkgcheck（`pkgcore/pkgcheck-action`，args 无 `--net`，`--exit=NonexistentDeps`），只做元数据/依赖类检查，**不做联网 URL 复查**。
- `.github/workflows/emerge-on-pr.yml`：每个包在 `amd64-desktop-openrc`、`amd64-desktop-systemd` 两个 profile 上各 emerge 一次；`KEYWORDS` 含 `arm64` 或 `~arm64` 的包，因为 arm64 没有 desktop-openrc 的 stage3，所以另在 `ubuntu-24.04-arm` runner 上补充执行 `arm64-desktop-systemd` 一条腿。每条腿都判 **elog 硬门**：`PORTAGE_ELOG_CLASSES="qa warn error"`、`PORTAGE_ELOG_SYSTEM="save"`，post-emerge 步骤扫 `/var/log/portage/elog/*` **文件**（非 stdout），任一 qa/warn/error elog 即 `exit 1`。预编译包只要上游发布了 arm64 产物就加 `~arm64`，手上没有 arm 机器也加：因为 blob 不在本地编译、各 arch 共用同一套 `src_install`，所以 CI 那条腿真装一次、elog 门照判就够，出了 arch 相关的问题按报告修。源码包没有这个例外：因为写进 `KEYWORDS` 等于声明这个 arch 已经建过，所以新加的每个 arch 都要自己真编过再加；CI 的 arm64 腿只在已经 keyword 了 arm64 的包上执行，是复核而非代替本地构建。手上有 arm 设备的一律自己先验过再加。

`gzh` 尚未覆盖的门，收尾阶段须手动补充执行：
- **真实安装与 elog 检查**：`gzh build` 已使用隔离的 `PORTAGE_LOGDIR` 保存并检查 `qa`、`warn`、`error` elog，但 `ebuild install` 不解析依赖，也不执行真实合并。收尾仍须执行 `gzh merge`，并检查其保存的 elog 文件；本地结果不能替代 overlay CI。
- **联网 DeadUrl 复查**：收尾执行 `gzh urls`，它执行 `pkgcheck scan --git-remote <canonical> --commits="$(git merge-base <canonical>/master HEAD)..HEAD" --net`。因为裸 `--commits` 比的是 fork 那份落后的 `origin`、会把无关包一起扫进来，所以范围必须用 merge-base 显式限死。这一段 CI 与 `gzh qa` 都不执行，属 PR 勾选项，须手动补充执行。联网结果只是关于本机的证据：因为在本机返回 403 的边缘节点在其他网络可能正常，所以判定一个 URL 失效之前须换一个网络复验。CI 的 pkgcheck 不带 `--net`，不会触发任何联网检查项。

勿因本地 `gzh` 全绿即认为 CI 会绿。良性 elog（Unresolved soname/CONFIG_CHECK/binchecks）注明放行，真问题改 ebuild。
