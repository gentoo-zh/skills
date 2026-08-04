# gentoo-zh skills

本仓库提供 Gentoo overlay 开发 skill、gentoo-zh 仓库专用工作流和确定性维护工具 `gzh`。通用 skill 只处理由 Gentoo 官方资料定义的 ebuild、依赖、Manifest、eclass、license、构建和 QA 语义。目标 overlay 的 remote、branch、keyword、CI、提交和发布规则必须来自该仓库的实时政策。

## 环境要求

- Python 3.11 或更新版本；
- Git；
- 目标 overlay 的可写开发副本；
- Portage、`pkgdev` 和 `pkgcheck`；
- 读取 GitHub issue 或准备 PR 时所需的 GitHub CLI `gh`；
- Python `venv` 和 `pip`。安装缺失依赖时还需要网络连接和可用的 CA 证书。

使用 gentoo-zh 专用命令时，设置 `GZH_OVERLAY_DIR` 或从 overlay Git 工作树内运行 `gzh`。不要把 Portage 同步目录当作开发工作树。

## 安装

从仓库根目录执行：

```bash
./install.sh
```

默认安装四个 skill，并在专用 venv 中安装 `gzh`：

- `gentoo-overlay-development`：通用 Gentoo overlay 包生命周期、repository metadata、eclass 和验证；
- `gzh-version-bump`：gentoo-zh 单包升级；
- `gzh-bump-from-issues`：gentoo-zh bump-reminder issue 批处理；
- `gzh-maintain-skills`：本仓库的证据审计和有界维护迭代。

默认安装目标为 Codex 和 OpenCode。新环境同时安装 Codex、Claude Code 和 OpenCode 时，使用两个互不重叠的发现目录：

```bash
CODEX_HOME="$HOME/.codex" ./install.sh codex claude opencode
```

该命令把 Codex skill 安装到 `$HOME/.codex/skills`，并让 Claude Code 和 OpenCode 共用 `$HOME/.claude/skills`。OpenCode 会扫描多个兼容目录；同名 skill 出现在两个可发现目录时，加载结果取决于扫描顺序，因此安装器会在写入前拒绝重复路径。

已有受管理安装需要迁移目录时，先移除 skill，再执行新的安装命令；`gzh` 环境不受 `--skills-only` 影响：

```bash
./install.sh --uninstall --skills-only
CODEX_HOME="$HOME/.codex" ./install.sh codex claude opencode
```

也可以只安装指定目标或组件：

```bash
./install.sh codex claude --copy
./install.sh claude --skills-only
./install.sh opencode --skills-only
./install.sh --gzh-only
```

各目标使用以下发现目录：

- Codex：未设置 `CODEX_HOME` 时使用 `$HOME/.agents/skills`，否则使用 `$CODEX_HOME/skills`；
- Claude Code：`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills`；
- OpenCode：只安装该目标时使用 `${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills`，与其他目标共同安装时复用兼容目录；
- `gzh`：环境位于 `${GZH_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/gentoo-zh-skills/gzh}`，启动器位于 `${GZH_BIN_DIR:-$HOME/.local/bin}/gzh`。

安装器会检查所有已知发现目录和已经记录的安装。环境变量 `OPENCODE_DISABLE_EXTERNAL_SKILLS=1` 或 `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` 会缩小 OpenCode 的发现范围，但只有 OpenCode 运行时也使用相同设置，该范围才保持一致。同名路径不归本项目管理、多个目录会导致重复加载或目标路径无法安全更新时，安装会在写入前停止。`gzh` venv 继承系统 site-packages，以便读取 Gentoo 安装的 Portage 模块。

安装器把每个目标的客户端、模式、来源目录和 skill 成员写入 `${XDG_DATA_HOME:-$HOME/.local/share}/gentoo-zh-skills/skill-installations.json`。更新时依据该记录增加新 skill，并删除已从当前 bundle 移除但仍由本项目管理的 skill。路径所有权或状态文件不符合记录时，操作停止；单个目标的文件切换或状态写入失败时，原文件和原记录一并恢复。

检查或移除本项目管理的安装：

```bash
./install.sh --status
./install.sh --uninstall
```

状态检查验证受管理文件、符号链接和 `gzh` 启动器，不代替客户端内的发现测试。客户端已运行但未发现新 skill 时，按该客户端的当前官方文档重新加载或重启。

## 选择工作流

开发独立维护的 overlay 时，使用 `$gentoo-overlay-development`。它覆盖现有包、新包、keyword、移动、删除、repository metadata、profile 和 eclass 改动。只有目标仓库的实时政策明确给出工作树身份、支持的操作、canonical base、keyword、必需命令、验证门、提交和发布程序后，skill 才允许写入。仓库已有经过复核的专用 skill 时，优先使用专用工作流。

gentoo-zh 单包升级使用 `$gzh-version-bump`。它核对实时仓库约定、上游来源、依赖、patch、license、架构产物、Manifest、pkgcheck、构建、安装 elog 和提交门。

批量处理 nvchecker bump-reminder issue 使用 `$gzh-bump-from-issues`。它逐条读取 issue 和评论，将可执行项交给单包 skill，并记录 skip、escalate 和失败。批量 skill 不自动 push 或创建 PR。

维护本仓库、处理来源漂移、修复 CI、扩展验证或提取可复用行为时，使用 `$gzh-maintain-skills`。一次迭代只修改一个有证据支持的行为边界；没有行为变化时，干净的 no-op 是有效结果。

PR 正文需要中文时，使用 `chinese-skill` 检查措辞和术语。`pkgdev` 生成的英文 subject 保持原文，不翻译。

## 通用验证工具

通用工具位于 `gentoo-overlay-development` skill。依赖解析器只读取已经提取的 EAPI metadata，不执行 ebuild：

```bash
python3 .agents/skills/gentoo-overlay-development/scripts/dependency_analyzer.py \
  --input /absolute/path/dependencies.json \
  --output /tmp/dependency-report.json
```

`dependencies.json` 可包含 `eapi`、明确的 `use` 状态及该 EAPI 支持的依赖字段。工具使用 Portage API 解析条件、atom、blocker 和 slot operator，记录输入字节数与 SHA-256，并拒绝超过 1 MiB 的输入。报告只证明语法解析结果；包行为和 provider 仍需上游资料与目标仓库解析结果证明。

通用 pkgcheck runner 要求干净的 Git 工作树，并把 cache 和报告写在 overlay 外：

```bash
python3 .agents/skills/gentoo-overlay-development/scripts/qa_runner.py \
  --repository /absolute/path/to/overlay \
  --adapter-id <adapter-id> \
  --canonical-repository <owner/repository> \
  --target category/package \
  --output /tmp/pkgcheck-report.json
```

runner 记录仓库 revision、命令、pkgcheck 版本、来源 lock、完整 finding 和网络检查状态，并限制运行时间和输出大小。只有实时仓库政策要求联网扫描时才添加 `--net`。调用方提供的 adapter 和仓库身份在报告中标记为尚未验证，因此报告不能替代仓库能力解析或发布程序。

gentoo-zh 的常用确定性命令优先使用短名称。开始写入前先核对 adapter、仓库身份和 Git 状态：

```bash
export GZH_OVERLAY_DIR=/path/to/overlay
gzh repo
gzh doctor --operation repository-write-preflight
gzh plan category/package 1.2.3
gzh latest category/package
gzh bump category/package 1.2.3
gzh lint category/package/package-1.2.3.ebuild
gzh manifest category/package/package-1.2.3.ebuild --distdir /writable/distfiles
gzh qa category/package --min-severity error
gzh build category/package/package-1.2.3.ebuild
gzh merge category/package/package-1.2.3.ebuild
gzh diff category/package/package-1.2.2.ebuild category/package/package-1.2.3.ebuild
gzh commit category/package/package-1.2.3.ebuild
gzh urls
```

`gzh lint` 只执行已实现的快速结构检查，不代替 Gentoo Devmanual、eclass reference、`pkgcheck` 或实际安装。目标仓库规定的 `lint`、Manifest、package QA、构建、安装 elog 和网络检查仍是硬门。

附加工具按改动面启用，不要求每个包无条件执行全部命令：

| 改动面 | 命令 | 结果 |
| --- | --- | --- |
| 依赖或 USE | `gzh deps <ebuild> --use +flag` | 从匹配的 Portage cache 解析依赖，不执行 ebuild |
| distfile 或架构产物 | `gzh artifacts <Manifest> --evidence <json>` | 核对每个 `DIST` 条目、来源记录和可选本地 digest |
| 预编译 ELF | `gzh binary <path>` | 使用 `file` 和 `readelf` 静态检查，不执行目标文件 |
| 安装结果 | `gzh image <image-root>` | 检查 symlink、mode、desktop、systemd 和 ELF metadata |
| 支持的测试矩阵 | `gzh test =category/package-version::gentoo-zh -x` | 通过 `pkgdev tatt` 保存有界测试证据 |
| 本地或 SSH 安装 | `gzh exec =category/package-version::gentoo-zh --executor <name> -x` | 运行同一 exact-atom 和隔离 elog 合同，并保存可校验记录 |

`gzh exec` 从 `${GZH_EXECUTOR_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/gzh/executors.toml}` 读取严格配置。SSH executor 还需要为当前 commit 的每个改动文件重复传入 `--path`。host、port、identity file、远端开发副本路径和依赖安装授权只保存在用户配置中，不写入 skill：

```toml
version = 1

[executors.local]
type = "local"
allow_dependency_install = true

[executors.builder]
type = "ssh"
host = "build.example"
user = "portage"
port = 22
identity_file = "/absolute/path/to/identity"
remote_overlay_path = "/srv/gentoo-zh-overlay"
allow_dependency_install = true
```

批量队列和发布收尾也使用短命令。队列 selector 保存 canonical base、配置条目、issue revision、bot marker 和选择原因。publication 命令只生成计划或读取状态，不会 push、创建 PR、merge 或删除 worktree：

```bash
gzh bump-issues --autobump off --issue 11700 --limit 1000
gzh pr-plan --title 'category/package: add 1.2.3' --body /tmp/pr-body.md
gzh ci 11714 --watch
gzh batch-report reconcile <report.json> --expected-sha256 <sha256>
gzh batch cleanup <report.json> --dry-run
```

新的可恢复批次应使用 `gzh batch-report create --format json`。JSON report 保留原始 QA、skip、failure 和风险字段，并追加 `local_commit -> pushed -> pr_open -> checks_passed -> merged` 观察记录。旧的 Markdown report 仍可 checkpoint，但不能依赖文本解析执行 publication reconciliation。

旧名称继续兼容现有脚本，但 `gzh --help` 和 shell completion 只显示短名称：

| 短名称 | 兼容的旧名称 |
| --- | --- |
| `latest` | `upstream-version` |
| `bump` | `bump-scaffold` |
| `diff` | `diff-ebuild` |
| `parse` | `ebuild-parse` |
| `qa` | `pkgcheck` |
| `urls` | `pkgcheck-commits` |
| `build` | `build-test` |
| `merge` | `verify-install` |

队列快照、批次报告、PR plan、executor 证据和 triage 记录位于 `${GZH_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/gentoo-zh-skills}`。自定义路径必须是绝对路径，且不能位于 overlay 工作树内。队列结果只有在整体和每个项目的截断字段均为 `false` 时才完整。

## 更新

更新干净的 checkout，并刷新受管理的 skill 和 `gzh` 环境：

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

更新脚本按远程 URL 查找唯一的 canonical remote，fetch 其 `master`，再执行 fast-forward-only merge。当前分支不是 `master`、本地提交未同步、工作树有修改、HEAD detached、remote 缺失或不唯一，以及无法 fast-forward 时都会停止。脚本不会 reset、覆盖本地改动、自动改写规则或刷新来源 lock。

## 证据和持续维护

规则按以下顺序取证：

1. 目标 overlay 的实时政策、workflow、模板和仓库自有文档；
2. Gentoo PMS、Devmanual、适用于当前场景的 GLEP、QA policy、eclass reference 和官方工具文档；
3. 上游 release、tag、源码、构建 metadata、license 和发布产物；
4. 当前官方实现和完整历史，仅用于证明实现行为；
5. 其他 overlay 和衍生资料，仅用于发现候选问题。

相似目录结构不能证明两个 overlay 使用相同的 remote、branch、keyword、CI、review 或发布规则。只约束 Gentoo ebuild repository 的 GLEP 也不会自动成为通用 overlay 规则。`gentoo-tree-lessons` 只提供候选 commit 和测试样本，任何硬规则都需要当前官方资料或目标仓库政策支持。

机器可读来源位于 [`sources.json`](.agents/skills/gentoo-overlay-development/references/sources.json)，当前登记 58 个官方或明确标注的次级来源。人工复核后的 revision 或 SHA-256 位于 [`source-lock.json`](.agents/skills/gentoo-overlay-development/references/source-lock.json)。查询时必须指定 `--scope`、`--all-scopes` 或 `--id`：

```bash
python3 .agents/skills/gentoo-overlay-development/scripts/source_manager.py \
  audit --all-scopes --fail-on-drift
```

每条 HTTP 或 MediaWiki 请求都在独立子进程内执行，父进程设置 60 秒总时限。HTTP 正文上限为 16 MiB，MediaWiki 正文上限为 2 MiB，Git 输出上限为 256 KiB，并发 worker 数限制为 1 至 16。

定期 workflow 从经过认证的 artifact 恢复 SQLite 状态，优先恢复同一 skills revision 下未完成的计划，再运行 allowlist 队列。任务保存完整输入、SHA-256、adapter、canonical repository、游标、重试次数和结构化报告；QA 历史采集在计划最后运行，因此前置门失败不会推进游标。证据写入与队列成功使用同一事务。

历史 commit 只进入候选状态。候选需要单独复核官方证据、建立显式证据关联并完成 promotion checklist 后，才能成为规则。定期 workflow 不会自动修改 skill、刷新来源 lock、commit、push、发布或修改外部仓库。手动 `workflow_dispatch` 只能对最新且经过认证的状态执行明确的 candidate transition 或 evidence link；定时、初始化和恢复路径不能隐式晋升规则。

状态压缩只删除已全部成功的旧计划，并删除旧的例行运行正文、压缩旧候选发现报告；pending、running 和 blocked 计划全部保留。定期 workflow 最多保留 16 个计划，未完成计划达到 17 个时会在删除前停止。压缩仍保留原始 SHA-256、规范化来源、候选、状态转换、复核关系和每个仓库的最新游标。待复核候选超过 512、总候选超过 4096 或压缩后的数据库超过 96 MiB 时，周期停止并更新维护 issue，不会静默丢弃证据。

维护合同见 [`gzh-maintain-skills`](.agents/skills/gzh-maintain-skills/SKILL.md)，分层设计见 [`overlay-architecture.md`](.agents/skills/gzh-maintain-skills/references/overlay-architecture.md)。安装目标和 skill 格式以各项目当前官方文档为准：

- [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)；
- [Claude Code skills](https://code.claude.com/docs/en/skills)；
- [OpenCode skills](https://opencode.ai/docs/skills)；
- [Agent Skills specification](https://agentskills.io/specification)。

仓库采用渐进加载：客户端先读取四个 skill 的 `name` 和 `description`，只在触发后加载对应 `SKILL.md`，再按任务读取一层 references 或直接执行 scripts。验证器限制 `SKILL.md` 不超过 500 行，并要求每个 reference 从所属 `SKILL.md` 直接可发现。客户端专用的 OpenAI UI metadata 位于 `agents/openai.yaml`；共同的 `SKILL.md` 只保留 `name` 和 `description`，避免加入其他客户端无法稳定解释的 frontmatter。

## 验证

创建测试环境后执行：

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e './gzh[test]'
.venv/bin/python scripts/validate_repository.py
.venv/bin/python -m pytest -q gzh/tests tests
.venv/bin/python scripts/eval_runner.py static
.venv/bin/python .agents/skills/gentoo-overlay-development/scripts/source_manager.py \
  audit --all-scopes --fail-on-drift
```

验证器检查 skill frontmatter、metadata、activation eval、英文正文、来源 schema 和 lock、局部链接、reference 可发现性及可执行脚本。静态 eval 当前覆盖 67 个 activation、exclusion 和行为案例；外部 runner 通过显式 JSON protocol 接入，不会把预期答案写入待测 prompt。安装测试只写入临时目录。

## 目录

- [`.agents/skills`](.agents/skills)：可安装 skill；
- [`gzh`](gzh)：Python CLI 与单元测试；
- [`scripts`](scripts)：安装、更新和仓库验证；
- [`docs/devmanual.md`](docs/devmanual.md)：本项目使用的 Gentoo 官方文档索引；
- [`AGENTS.md`](AGENTS.md)：本仓库维护约定。

`docs/superpowers` 保存早期设计和实施记录，只用于历史背景。运行时合同由目标仓库政策、当前 skill、确定性测试和目标仓库 CI 共同确定。
