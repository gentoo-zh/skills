# gentoo-zh skills

本仓库提供 gentoo-zh overlay 的版本升级技能和确定性维护工具 `gzh`。技能负责证据选择、维护流程与停止条件，`gzh` 负责可重复执行和测试的仓库操作。

## 环境要求

- Python 3.11 或更新版本；
- Git；
- 可写的 gentoo-zh overlay 开发副本；
- Portage、`pkgdev` 和 `pkgcheck`；
- 批量读取 issue 或准备 PR 时需要 GitHub CLI `gh`；
- Python 的 `venv` 和 `pip`；缺少依赖时还需要网络连接与可用的 CA 证书。

不要把 Portage 同步到 `/var/db/repos/gentoo-zh` 的副本作为开发工作树。设置 `GZH_OVERLAY_DIR`，或从 overlay Git 工作树内运行 `gzh`。

## 安装

从仓库根目录执行：

```bash
./install.sh
```

默认为 Codex 和 OpenCode 安装两个技能的符号链接，并在专用 venv 中安装 `gzh`：

- Codex：默认使用 `$HOME/.agents/skills`；显式设置 `CODEX_HOME` 时使用 `$CODEX_HOME/skills`；
- Claude Code：显式选择时使用 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills`；
- OpenCode：默认复用 Codex 的 `$HOME/.agents/skills`；Codex 使用显式 `CODEX_HOME` 时改用 OpenCode 原生目录；
- `gzh`：`${GZH_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/gentoo-zh-skills/gzh}`，启动器位于 `${GZH_BIN_DIR:-$HOME/.local/bin}/gzh`。

选择客户端或复制模式：

```bash
./install.sh codex claude --copy
./install.sh claude --skills-only
./install.sh opencode --skills-only
./install.sh --gzh-only
```

只安装 OpenCode 时，技能写入 `${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills`。同时选择多个客户端时，安装器会尽量复用一个兼容目录；如果所选目录会让 OpenCode 加载多个同名技能，则在写入前停止。遇到同名但不归本项目管理的路径时，本次安装不会写入任何技能或 `gzh` 环境。

`gzh` 使用专用 venv，但会继承系统 site-packages，以便读取 Gentoo 安装的 Portage 模块。安装后应确认 `${GZH_BIN_DIR:-$HOME/.local/bin}` 已加入 `PATH`。

检查或移除已管理的安装：

```bash
./install.sh --status
./install.sh --uninstall
```

默认的状态检查和卸载也会识别已有的 OpenCode 原生目录。需要只检查该目录时，执行 `./install.sh opencode --skills-only --status`。状态检查验证已管理文件和 `gzh` 启动器，不代替客户端内的发现与调用测试。

安装时如果客户端已经运行，而且技能没有出现，请按对应客户端的当前文档重新加载或重启客户端。

安装器默认采用 OpenAI 当前文档列出的 `$HOME/.agents/skills`。显式设置 `CODEX_HOME` 时保留 Codex 内置工具使用的 `$CODEX_HOME/skills`；状态检查、卸载和更新也会识别旧的 `$HOME/.codex/skills` 安装。安装 Codex 或 OpenCode 技能前，安装器会检查各客户端的兼容目录；发现其他目录已有同名技能时会停止，避免重复加载。

## 使用

设置开发工作树：

```bash
export GZH_OVERLAY_DIR=/path/to/overlay
gzh repo
gzh state-dir
```

单包升级使用 `$gzh-version-bump`。它读取实时 overlay 约定，核对上游、依赖、patch、license 和架构产物，然后执行 Manifest、pkgcheck、构建、install elog 与提交门。

批量处理 nvchecker bump-reminder issue 使用 `$gzh-bump-from-issues`。它逐条读取 issue 和评论，将可执行项交给单包技能，并记录 skip、escalate 和失败。批量技能不自动 push 或创建 PR。

队列快照、批次报告和 triage 记录位于 `${GZH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/gentoo-zh-skills}`。自定义路径必须是绝对路径；`gzh` 检测到路径位于 overlay 工作树内时会拒绝写入。队列结果包含 issue 总数、实际读取数和截断状态；只有队列的 `truncated` 和每个项目的 `comments_truncated` 都为 `false` 时，数据才完整。

PR 正文需要中文时，技能调用 `chinese-skill` 检查措辞和术语。`pkgdev` 生成的英文 subject 保持原文，不翻译。

常用确定性命令：

```bash
gzh upstream-version category/package
gzh bump-scaffold category/package 1.2.3
gzh lint category/package/package-1.2.3.ebuild
gzh manifest category/package/package-1.2.3.ebuild --distdir /writable/distfiles
gzh pkgcheck category/package --min-severity error
gzh build-test category/package/package-1.2.3.ebuild
gzh verify-install category/package/package-1.2.3.ebuild
gzh commit category/package/package-1.2.3.ebuild
gzh recommit category/package/package-1.2.3.ebuild
gzh pkgcheck-commits
```

`gzh triage skip` 和 `gzh triage resolve` 要求传入完整 issue 快照的 `updated_at` 与当前记录的 `event_id`；没有记录时使用 `none`。复用记录时，当前 `updated_at` 必须与记录的 `issue_updated_at` 完全相同；只有缺少该字段的旧记录才回退到 `recorded_at`。命令会在写入前查询 GitHub 的当前 issue revision，活动记录写入后还会复查；本地状态采用文件锁、原子替换和 compare-and-swap。证据或状态冲突时会停止，旧记录的 `skipped_at` 则作为 `recorded_at` 返回。

批次开始时，把完整的英文 Markdown 报告传给 `gzh batch-report create`，并保存返回的 `path` 与 `sha256`。只有协调 agent 可以写报告；每次分类、失败、检查、commit 或网络检查后，把完整报告传给 `gzh batch-report checkpoint <report-path> --expected-sha256 <sha256>`。报告名称通过 exclusive create 保证不会覆盖并行任务；checkpoint 使用文件锁、hash compare-and-swap 和原子替换，过期写入会停止。

`gzh recommit` 只用于提交后的检查迫使包内容继续修改时。它要求 canonical `master` 之上只有一个本地 commit，并通过 `pkgdev` 重建该 commit；普通提交仍使用 `gzh commit`。

`gzh lint` 只执行已实现的快速结构检查，不代替 Gentoo Devmanual、eclass reference、`pkgcheck` 或实际安装。

## 更新

更新干净的 checkout，并刷新已管理的技能副本和 `gzh` 环境：

```bash
./update.sh
```

仅从当前 checkout 刷新安装，不访问 Git：

```bash
./update.sh --installed-only
```

同时审计全部已登记来源并统计案例库：

```bash
./update.sh --references
```

更新脚本按远程 URL 查找唯一的 `gentoo-zh/skills` canonical remote，fetch 其 `master`，再执行 fast-forward-only merge。当前分支不是 `master`、本地 `master` 含有远程没有的 commit、工作树有修改、HEAD detached、canonical remote 缺失或不唯一，以及无法 fast-forward 时都会停止。脚本不会 reset 或覆盖本地改动。来源漂移、来源读取失败或案例库更新失败会返回非零状态；脚本不会自动改写规则或更新来源锁。

## 官方资料和持续维护

规则按以下顺序取证：

1. 目标 overlay 的实时 `AGENTS.md`、workflow、PR 模板和包历史；
2. Gentoo PMS、Devmanual、GLEP、官方 eclass reference 和官方工具；
3. 上游 release、tag、源码、构建 metadata、license 和发布产物；
4. 当前 Gentoo tree 中真正可比的实现；
5. GURU 维护经验和衍生案例。

GURU 的仓库专属政策不能移植到 gentoo-zh。`gentoo-tree-lessons` 只用于寻找候选 commit 和测试样本，不能直接产生硬规则。

License 与分发权限使用独立的[验证流程](.agents/skills/gzh-version-bump/references/license-validation.md)。该流程分别核对软件本体、捆绑组件和发布产物，并区分软件授权、网站条款、隐私政策与第三方声明。gentoo-zh 在 2026-08-02 合并的稽核修正了 42 个错误的 `LICENSE`、3 个不含软件授权条款的 license 文件，以及 19 个缺漏的 `RESTRICT`；这些数据用于回归测试，当前上游条款和官方 Gentoo 文档仍是判定依据。

机器可读来源在 [`sources.json`](.agents/skills/gzh-version-bump/references/sources.json)，人工审查后的指纹在 [`source-lock.json`](.agents/skills/gzh-version-bump/references/source-lock.json)。定期 workflow 只报告漂移并建立追踪 issue，不会自动把变化写成新规则。审查和升级步骤见 [`continuous-improvement.md`](.agents/skills/gzh-version-bump/references/continuous-improvement.md)。

客户端发现路径和格式以各项目的当前文档为准：

- [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)；
- [Claude Code skills](https://code.claude.com/docs/en/skills)；
- [OpenCode skills](https://opencode.ai/docs/skills)；
- [Agent Skills specification](https://agentskills.io/specification)。

## 验证

创建测试环境后执行。`.venv/` 已排除在 Git 跟踪之外，不会阻止更新脚本：

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install --no-build-isolation -e './gzh[test]'
.venv/bin/python scripts/validate_repository.py
.venv/bin/python -m pytest -q gzh/tests tests
```

验证器检查技能 frontmatter、客户端 metadata、activation eval 文件结构、英文技能正文、来源 schema 和 lock、局部链接及可执行脚本。安装测试使用临时目录，不会修改用户配置。

## 目录

- [`.agents/skills`](.agents/skills)：可安装技能；
- [`gzh`](gzh)：Python CLI 与单元测试；
- [`scripts`](scripts)：安装、更新和仓库验证；
- [`docs/devmanual.md`](docs/devmanual.md)：本项目使用的官方 Gentoo 文档索引；
- [`AGENTS.md`](AGENTS.md)：本仓库维护约定。

`docs/superpowers` 保存早期设计和实施记录，只用于历史背景。运行时合同由实时 overlay 规则、当前技能、`gzh` 测试和 overlay CI 共同确定。
