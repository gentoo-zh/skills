# gentoo-zh skills

本仓库提供 Gentoo overlay 开发 skill、gentoo-zh 仓库专用工作流和确定性维护工具 `gzh`。通用 skill 只处理由 Gentoo 官方资料定义的 ebuild、依赖、Manifest、eclass、license、构建和 QA 语义。目标 overlay 的 remote、branch、keyword、CI、提交和发布规则必须来自该仓库的实时政策。

## 环境要求

- Python 3.11 或更新版本；
- Git；
- 目标 overlay 的可写开发副本；
- Portage、`pkgdev` 和 `pkgcheck`；
- 使用 ELF 或 installed-image 检查时所需的 `file`、binutils 和 `pax-utils`；
- 读取 GitHub issue 或准备 PR 时所需的 GitHub CLI `gh`；
- Python `venv` 和 `pip`。安装缺失依赖时还需要网络连接和可用的 CA 证书。

使用 gentoo-zh 专用命令时，设置 `GZH_OVERLAY_DIR` 或从 overlay Git 工作树内运行 `gzh`。不要把 Portage 同步目录当作开发工作树。

## 安装

### 直接安装 skill 和 gzh

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

安装或状态检查发现启动器目录不在 `PATH` 时，会输出非致命警告；安装器不会修改 shell 配置。把警告中的目录加入 `PATH` 后，即可直接运行 `gzh`，否则仍可使用启动器的完整路径。

安装器会检查所有已知发现目录和已经记录的安装。同名路径不归本项目管理、多个目录会导致重复加载或目标路径无法安全更新时，安装会在写入前停止。OpenCode 运行时应使用与安装时相同的 skill 发现设置；否则实际扫描范围可能扩大，安装器的重复检查不能代表运行时状态。`gzh` venv 继承系统 site-packages，以便读取 Gentoo 安装的 Portage 模块。

安装器把每个目标的客户端、模式、来源目录和 skill 成员写入 `${XDG_DATA_HOME:-$HOME/.local/share}/gentoo-zh-skills/skill-installations.json`。更新时依据该记录增加新 skill，并删除已从当前 bundle 移除但仍由本项目管理的 skill。路径所有权或状态文件不符合记录时，操作停止；单个目标的文件切换或状态写入失败时，原文件和原记录一并恢复。

检查或移除本项目管理的安装：

```bash
./install.sh --status
./install.sh --uninstall
```

状态检查验证受管理文件、符号链接和 `gzh` 启动器，不代替客户端内的发现测试。客户端已运行但未发现新 skill 时，按该客户端的当前官方文档重新加载或重启。

### 使用 Codex 和 Claude Code 插件

`gentoo-overlay-skills` 插件复用同一套四个 skill，不复制源码。插件只安装说明和辅助脚本，不安装 `gzh`。先从当前 checkout 安装 `gzh`：

```bash
./install.sh --gzh-only
```

不要在同一个客户端中同时安装本插件和同名的独立 skill。迁移现有受管理安装时，先删除原有 skill；OpenCode 需要继续使用其原生 skill 目录：

```bash
./install.sh --uninstall --skills-only
./install.sh opencode --skills-only
```

从仓库根目录注册本地 marketplace，再安装插件：

```bash
codex plugin marketplace add ./
codex plugin add gentoo-overlay-skills@gentoo-zh-skills

claude plugin marketplace add ./
claude plugin install gentoo-overlay-skills@gentoo-zh-skills
```

安装后应启动新的 Codex CLI session。Claude Code 的当前 session 应执行 `/reload-plugins`，不支持该命令时重启客户端。

插件更新依赖新的 manifest 版本。更新 checkout 后，Codex 重新执行安装命令；Claude Code 先刷新 marketplace，再更新插件：

```bash
./update.sh
codex plugin add gentoo-overlay-skills@gentoo-zh-skills
claude plugin marketplace update gentoo-zh-skills
claude plugin update gentoo-overlay-skills@gentoo-zh-skills
```

更新后同样需要启动新的 Codex CLI session，并在 Claude Code 中执行 `/reload-plugins` 或重启客户端。

Codex 当前没有独立的 plugin update 或 rollback 命令。升级前记录 checkout 的准确 commit；需要降级时先停止，不要直接修改客户端 cache，也不要把未验证的 remove-and-add 当作回退。删除插件时使用客户端命令：

```bash
codex plugin remove gentoo-overlay-skills@gentoo-zh-skills
codex plugin marketplace remove gentoo-zh-skills

claude plugin uninstall gentoo-overlay-skills@gentoo-zh-skills
claude plugin marketplace remove gentoo-zh-skills
```

OpenCode 的 plugin 是 JavaScript、TypeScript 或 npm 扩展，不是 skill bundle。因为 OpenCode 直接发现 Agent Skills，所以继续使用 `./install.sh opencode`，不要把 Codex 或 Claude Code 的 plugin cache 当作 OpenCode 安装源。

## 选择工作流

开发独立维护的 overlay 时，使用 `$gentoo-overlay-development`。它覆盖现有包、新包、keyword、移动、删除、repository metadata、profile 和 eclass 改动。只有目标仓库的实时政策明确给出工作树身份、支持的操作、canonical base、keyword、必需命令、验证门、提交和发布程序后，skill 才允许写入。仓库已有经过复核的专用 skill 时，优先使用专用工作流。

gentoo-zh 单包升级使用 `$gzh-version-bump`。它核对实时仓库约定、上游来源、依赖、patch、license、架构产物、Manifest、pkgcheck、构建、安装 elog 和提交门。

批量处理 nvchecker bump-reminder issue 使用 `$gzh-bump-from-issues`。它逐条读取 issue 和评论，将可执行项交给单包 skill，并记录 skip、escalate 和失败。批量 skill 不自动 push 或创建 PR。

维护本仓库、处理来源漂移、修复 CI、扩展验证或提取可复用行为时，使用 `$gzh-maintain-skills`。一次迭代只修改一个有证据支持的行为边界；没有行为变化时，干净的 no-op 是有效结果。

PR 正文需要中文时，使用 `chinese-skill` 检查措辞和术语。先核实原文事实，再按 Gentoo 和仓库术语改写为自然中文；不要逐词翻译，也不要补写未经证实的原因。`pkgdev` 生成的英文 subject 保持原文，不翻译。

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
gzh plan category/package 1.2.3 --package-model source
gzh latest category/package
gzh bump category/package 1.2.3
gzh lint category/package/package-1.2.3.ebuild
gzh manifest category/package/package-1.2.3.ebuild --distdir /writable/distfiles
gzh qa category/package --min-severity error
gzh qa category/package --profile stable --arch amd64
gzh deps inspect category/package/package-1.2.3.ebuild
gzh deps diff category/package/package-1.2.2.ebuild category/package/package-1.2.3.ebuild
gzh deps reverse dev-libs/example
gzh build category/package/package-1.2.3.ebuild
gzh merge category/package/package-1.2.3.ebuild
gzh diff category/package/package-1.2.2.ebuild category/package/package-1.2.3.ebuild
gzh commit category/package/package-1.2.3.ebuild
gzh urls
```

`gzh lint` 只执行已实现的快速结构检查，不代替 Gentoo Devmanual、eclass reference、`pkgcheck` 或实际安装。它会提示继承 `unpacker` 却绕过 `unpack_deb`，以及在 `src_install` 中解包的情况；这些结果需要人工核对，不表示所有自定义格式都有错误。目标仓库规定的 `lint`、Manifest、package QA、构建、安装 elog 和网络检查仍是硬门。

附加工具按改动面启用，不要求每个包无条件执行全部命令：

| 改动面 | 命令 | 结果 |
| --- | --- | --- |
| 单个 ebuild 的依赖或 USE | `gzh deps inspect <ebuild> --use +flag` | 验证现有 Portage cache；缺失或过期时，在隔离目录为当前工作树生成 metadata |
| 两个版本的依赖差异 | `gzh deps diff <old-ebuild> <new-ebuild>` | 比较 potential declaration；指定完整 USE 状态时另给 reduced delta |
| 直接反向依赖候选 | `gzh deps reverse <atom>` | 通过 `pquery` 查询已配置 ebuild repository 的 raw potential 关系 |
| profile 或 architecture QA 范围 | `gzh qa <target> --profile stable --arch amd64` | 使用 `pkgcheck` 官方 selector 缩限静态 QA 范围 |
| distfile 或架构产物 | `gzh artifacts <Manifest> --evidence <json>` | 分别记录产物身份、内容可检查状态和 Portage fetch 状态；指定 `--distdir` 时再核对本地 digest |
| release archive 的 license 材料 | `gzh license <archive>` | 不解压 archive，记录 license-like 文件的路径、大小和 SHA-256 |
| 预编译 ELF | `gzh binary <path>` | 使用 `file`、`readelf` 和 `lddtree` 检查 ELF 及 host-visible runtime dependency，不执行目标文件 |
| 预编译安装结果 | `gzh image <image-root> --inventory-evidence <new-relative-file> --require-non-elf-allowlist [--allow-executable <path>]` | 检查 symlink、mode、desktop、systemd、ELF metadata 和 executable non-ELF allowlist，并把完整文件清单写入独立证据文件 |
| 支持的测试矩阵 | `gzh test =category/package-version::gentoo-zh -x` | 通过 `pkgdev tatt` 保存有界测试证据 |
| 本地或 SSH 安装 | `gzh exec =category/package-version::gentoo-zh --executor <name> -x` | 预演 exact atom 的安装计划，确认授权后安装，并保存可校验记录 |

`gzh deps inspect` 和 `gzh deps diff` 按以下规则取得 dependency metadata：

- 先读取不超过 1 MiB 的一般 ebuild 文件，并核对现有 `metadata/md5-cache` 的 `_md5_`。
- 单独核对 ebuild 无法证明 inherited eclass 未漂移。含 `_eclasses_`、缺失或过期的 cache 都由官方 `egencache --external-cache-only` 在私有临时目录中重新生成。
- Portage 会读取当时的 ebuild 和 eclass，但不会写入 overlay 或系统 cache。报告保留 ebuild 与生成结果的 hash；ebuild 在生成期间变化时，命令会失败。
- 工具不会把全部 eclass、profile 和 repository configuration 封存为不可变快照。并发修改这些输入时，应停止并重新执行。

`diff` 始终保留 disabled USE branch 的 potential declaration 差异。只有两个版本都提供完整的 USE 状态时，报告才增加 reduced delta。slot、blocker 和条件变化只是复核候选，不能证明 provider 兼容性、ABI 或 revbump 需求。

`gzh deps reverse` 调用 `pquery --raw --ebuild-repos --restrict-revdep`，结果只表示已配置 ebuild repository 中可能存在直接依赖的版本。它不解析 active profile，不计算 transitive graph，也不判断 ABI。`gzh qa` 的 `--profile` 和 `--arch` 可重复使用；未指定时保留 `pkgcheck` 的默认范围。selector 只影响静态 QA，不代表对应 profile 或 architecture 已完成构建和安装。

`gzh plan` 要求明确指定 `--package-model source|prebuilt`。只有确认安装的程序和库均由源码构建，且不安装上游提供的预编译内容、JVM 字节码、平台安装包或可执行脚本集合时，才使用 `source`；只要包含其中一项，就使用 `prebuilt`。工具会检查 `-bin` 包名、`QA_PREBUILT`、`rpm.eclass`，以及 `SRC_URI` 引用的 AppImage、DEB、RPM、JAR、独立可执行文件和带架构的压缩包。源码构建产生的 JAR 等输出文件不会因此被误判。未命中信号不能证明该包是源码包；tar、ZIP 或脚本集合的内容不明确时，仍须根据上游内容分类。

prebuilt bump 使用 `gzh plan <package> <version> --package-model prebuilt --assets-evidence <json>`，并提供前后两个 release 的完整产物清单。工具会按 architecture 比较新增、删除或改名的文件，并要求每项变化都有明确决定；清单缺失、不完整或变化未处理时，计划不会进入可执行状态。分类和清单都是经过复核的输入，不是上游真实性证明；release URL、文件名、architecture 和实际内容仍须依据上游原始资料核对。

`gzh artifacts` 的 evidence 为每个 `DIST` 文件分别保存 `inspection_available`、`portage_fetch_state` 和对应证据。人工下载成功不能代替 Portage 默认 fetch；工具也不会自行认证调用方提供的 CI 或来源说明。`gzh binary` 会拒绝缺失 interpreter、未解析的 `DT_NEEDED` 和未展开的 AppImage、ZIP、SquashFS、ar 或 tar 容器。runtime dependency 结果仅覆盖当前主机可见的 filesystem 与 ELF loader search path，不会判断 Gentoo provider、`dlopen` 目标或 helper process。

`gzh build` 在新的 evidence 目录中保存 ebuild hash、Git 状态、active ARCH/profile、限定环境、完整命令、bounded output 和 elog hash。它执行 phase 级构建与 image preparation，不解析依赖，也不执行真实 merge；仍须运行 `gzh merge` 或授权的 `gzh exec`，并以 overlay CI 作为最终验收。

`gzh merge` 和 `gzh exec` 先使用实际安装参数执行 `emerge --pretend`。依赖优先使用 binary package，`--usepkg-exclude` 强制目标版本从 ebuild 安装；计划中出现未授权的重建、升级、降级、卸载或未知操作时不会安装。本地执行会把 Portage 绑定到 `gzh repo` 选择的开发工作树，SSH executor 则使用经过校验的远端开发工作树。命令在同一个隔离目录中保存 `qa`、`warn` 和 `error` elog，任一受监控的 elog 都会保留为证据并使安装失败。active profile 使用 `eselect --brief profile show` 记录，`ARCH` 使用 `portageq envvar ARCH` 记录；环境输出包含多行或不完整时，安装会在预演前停止。

`gzh license` 支持未压缩、gzip、bzip2 或 xz 压缩的 tar，以及不含 ZIP64 metadata 的 ZIP archive。命令会在创建 archive parser 前限制 ZIP central directory 和 tar extended header，再限制成员数量、声明大小和 license-like 文件读取量。Zstandard tar 和 ZIP64 metadata 尚无等价的有界预检，因此命令会明确拒绝。报告只提供文件名 inventory 与 hash，不判断适用条款、license 兼容性、镜像权限或二进制再发布权限。相关结论仍须依据当前 release 的原始条款和 Gentoo 官方 license 文档人工复核。

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

批量队列和发布收尾也使用短命令。`include` 模式合并筛选结果与明确指定的 issue；`exact` 模式只读取并选择明确指定的 issue。schema v2 快照保存完整选择表达式、最终 issue 集合、canonical base、配置条目、issue revision 和 bot marker。publication 命令只生成计划、更新结构化状态或读取状态，不会 push、创建 PR、merge 或删除 worktree：

```bash
gzh bump-issues --autobump off --issue-mode include --issue 11700 --limit 1000
gzh bump-issues --issue-mode exact --issue 11700 --issue 11701
gzh pr-plan --title 'category/package: add 1.2.3' --body /tmp/pr-body.md
gzh ci 11714 --watch
gzh batch-report update <report.json> --expected-sha256 <sha256> --item-id <id> --state pushed --reason <reason> --evidence <evidence.json>
gzh batch-report reconcile <report.json> --expected-sha256 <sha256>
gzh batch cleanup <report.json> --dry-run
```

新的可恢复批次应使用 `gzh batch-report create --format json`。schema v2 要求绑定来源队列快照，并在处理前为每个 issue 创建 `null -> pending` 的 item；`pending` 只表示该项已入选，不表示分类失败。分类后只能进入 `blocked`、`local_committed` 或 `superseded_by_external_merge`，发布状态再按 `local_committed -> pushed -> pr_open -> checks_passed -> merged` 推进。命令会保留原始 QA、skip、failure、风险和未知扩展字段。旧的 Markdown report 仍可 checkpoint，但不能依赖文本解析更新状态或执行 publication reconciliation。

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

依赖命令在 `0.x` 期间采用相同的迁移规则：文档使用 `gzh deps inspect <ebuild>`，原有的 `gzh deps <ebuild>` 仍会静默转发到 `inspect`。转发不会向 stdout 写入提示，因此现有 JSON consumer 不需要立即修改。

### 从 pkgcheck 和 pkgdev 迁移

`gzh` 调用官方工具并增加有界 JSON 证据、仓库约束和失败关闭检查，不取代官方工具。语义相同的常用参数保留原拼写；涉及 staging、安装或测试副作用时，`gzh` 要求更明确的输入。

| 现有命令习惯 | `gzh` 命令 | 差异 |
| --- | --- | --- |
| `pkgcheck scan -p stable -a amd64 --exit error <target>` | `gzh qa -p stable -a amd64 --exit error <target>` | `gzh pkgcheck scan ...` 也可作为过渡；两者固定使用 JsonStream 并输出完整性、版本、timeout 和 truncation 证据 |
| `pkgcheck scan --profiles=stable,-exp --arches=amd64,-x86 <target>` | `gzh qa --profiles=stable,-exp --arches=amd64,-x86 <target>` | 保留官方逗号 selector，也接受可重复的 `--profile` 和 `--arch` |
| `pkgdev manifest -d <distdir> <ebuild>` | `gzh manifest -d <distdir> <ebuild>` | 固定强制重建目标 Manifest，并返回结构化结果 |
| `pkgdev commit -m <message> <path>` | `gzh commit -m <message> <path>` | 固定独立 QA、signoff 和 gentoo-zh subject 检查；必须明确列出 path |
| `pkgdev tatt -p <atom>` | `gzh test <exact-atom> -x` | `-x` 明确授权 Portage 配置改动和 package merge，证据写入新的有界目录 |

`gzh commit` 不接受 `pkgdev commit -a/-u` 的隐式全范围 staging，因为该映射会扩大调用方指定的文件范围。需要 pkgdev 尚未封装的高级参数时，继续直接使用 pkgdev，并按目标 repository 的实时政策补齐相同验证门。

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

## 发布

版本号必须在 `gzh/pyproject.toml`、`gzh.__version__`、`gzh --version` 和两个 plugin manifest 中保持一致，tag 使用 `v<version>`。发布前执行：

```bash
python3 scripts/release_check.py --mode source-only --tag v0.3.2
```

本仓库当前没有根目录 license 文件，也没有 `project.license` metadata。发布过程不会推断或添加法律条款；在仓库所有者明确选择 license 前，只发布 tag 对应的源码快照，不上传 wheel、sdist、可执行文件或其他自定义产物。GitHub 自动生成的源码 archive 以 tag 指向的 commit 内容为准，但外层压缩字节不属于稳定身份。`--mode package` 当前始终失败；根目录 license 和 package metadata 只能作为前提，后续还须实现并复核确定性的 rights-decision contract。

release tag 不会固定已有安装的版本。`install.sh` 安装当前 checkout，`update.sh` 仍从 canonical `master` 执行 fast-forward 后刷新受管理安装。插件 manifest 使用 release 版本，但本地 marketplace 仍指向当前 checkout。完整发布门、验证顺序和后续 license 决策见 [`RELEASING.md`](RELEASING.md)。

## 证据和持续维护

规则按以下顺序取证：

1. 目标 overlay 的实时政策、workflow、模板和仓库自有文档；
2. Gentoo PMS、Devmanual、适用于当前场景的 GLEP、QA policy、eclass reference 和官方工具文档；
3. 上游 release、tag、源码、构建 metadata、license 和发布产物；
4. 当前官方实现和完整历史，仅用于证明实现行为；
5. 其他 overlay 和衍生资料，仅用于发现候选问题。

相似目录结构不能证明两个 overlay 使用相同的 remote、branch、keyword、CI、review 或发布规则。只约束 Gentoo ebuild repository 的 GLEP 也不会自动成为通用 overlay 规则。`gentoo-tree-lessons` 只提供候选 commit 和测试样本，任何硬规则都需要当前官方资料或目标仓库政策支持。

机器可读来源位于 [`sources.json`](.agents/skills/gentoo-overlay-development/references/sources.json)，当前登记 75 个官方或明确标注的次级来源。人工复核后的 revision 或 SHA-256 位于 [`source-lock.json`](.agents/skills/gentoo-overlay-development/references/source-lock.json)。查询时必须指定 `--capability`、`--scope`、`--all-scopes` 或 `--id`：

```bash
python3 .agents/skills/gentoo-overlay-development/scripts/source_manager.py \
  audit --all-scopes --fail-on-drift
```

每条 HTTP 或 MediaWiki 请求都在独立子进程内执行，父进程设置 60 秒总时限。HTTP 正文上限为 16 MiB，MediaWiki 正文上限为 2 MiB，Git 输出上限为 256 KiB，并发 worker 数限制为 1 至 16。

定期 workflow 从经过认证的 artifact 恢复 SQLite 状态，优先恢复同一 skills revision 下未完成的计划，再运行 allowlist 队列。任务保存完整输入、SHA-256、adapter、canonical repository、游标、重试次数和结构化报告。固定队列包含来源审计、repository validator、release contract、测试和 diff 检查。QA 历史采集在计划最后运行，因此前置门失败不会推进游标。证据写入与队列成功使用同一事务。

历史 commit 只进入候选状态。候选需要单独复核官方证据、建立显式证据关联并完成 promotion checklist 后，才能成为规则。定期 workflow 不会自动修改 skill、刷新来源 lock、commit、push、发布或修改外部仓库。手动 `workflow_dispatch` 只能对最新且经过认证的状态执行明确的 candidate transition、原子化 batch transition 或 evidence link。batch manifest 必须列出每个 candidate 的准确 key、预期状态、目标状态和单独理由，且不能用于 promotion；定时、初始化和恢复路径不能隐式晋升规则。

状态压缩只删除已全部成功的旧计划，并删除旧的例行运行正文、压缩旧候选发现报告；pending、running 和 blocked 计划全部保留。定期 workflow 最多保留 16 个计划，未完成计划达到 17 个时会在删除前停止。压缩仍保留原始 SHA-256、规范化来源、候选、状态转换、复核关系和每个仓库的最新游标。待复核候选超过 512、总候选超过 4096 或压缩后的数据库超过 96 MiB 时，周期停止并更新维护 issue，不会静默丢弃证据。

维护合同见 [`gzh-maintain-skills`](.agents/skills/gzh-maintain-skills/SKILL.md)，分层设计见 [`overlay-architecture.md`](.agents/skills/gzh-maintain-skills/references/overlay-architecture.md)。安装目标和 skill 格式以各项目当前官方文档为准：

- [OpenAI Build skills](https://learn.chatgpt.com/docs/build-skills)；
- [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins)；
- [OpenAI Plugins](https://learn.chatgpt.com/docs/plugins)；
- [Claude Code skills](https://code.claude.com/docs/en/skills)；
- [Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference)；
- [Claude Code plugin discovery](https://code.claude.com/docs/en/discover-plugins)；
- [OpenCode skills](https://opencode.ai/docs/skills)；
- [OpenCode plugins](https://opencode.ai/docs/plugins)；
- [Agent Skills specification](https://agentskills.io/specification)。

仓库采用渐进加载：客户端的初始列表包含四个 skill 的 `name`、`description` 和 `SKILL.md` 路径。客户端只在触发后加载对应 `SKILL.md`，再按任务读取一层 references 或直接执行 scripts。验证器限制 `SKILL.md` 不超过 500 行，并要求每个 reference 从所属 `SKILL.md` 直接可发现。客户端专用的 OpenAI UI metadata 位于 `agents/openai.yaml`；共同的 `SKILL.md` 只保留 `name` 和 `description`，避免加入其他客户端无法稳定解释的 frontmatter。

常规 bump 先根据实际改动面选择 reference 和专项工具。小版本号、较短的 diff 或内容相同的 ebuild，都不能直接证明改动只涉及版本复制。处理新版本 archive 时，必须核实确切文件，并确认 artifact 的选择方式和 archive 结构没有变化。依赖、构建输入、USE 行为、patch、license 和安装布局也必须保持不变，才能按仅复制版本的 bump 处理。

该分类只会跳过无关的 dependency、binary、image 或 test-matrix 检查。目标仓库规定的 lint、Manifest、package QA、构建、干净安装、elog、diff 和联网检查不受该路由影响。

编写自定义 phase 或 QA 例外前，应先检查当前 Gentoo tree 中的同名包，再选择 source model、archive 格式、构建系统、安装布局和 eclass contract 相同的最小可比实现。`app-editors/vscode` 和 `www-client/google-chrome` 可作为预编译包的检索起点；`www-client/chromium` 是源码构建实现，不能因产品同属浏览器而直接套用。具体 ebuild 和历史必须在使用时重新读取，README 中的包名不是固定模板。

常规 bump 和 batch 默认只加载本地提交所需流程；两者的发布步骤位于条件 reference，只有明确请求发布时才加载。Codex 和 Claude Code plugin 共用 `.agents/skills`，各自保留独立 manifest 和 marketplace schema。`scripts/plugin_check.py` 检查版本、路径、skill 成员、symlink、特殊文件和 license 边界。OpenCode 继续使用共享 skill 格式，不声明不兼容的 plugin 能力。

## 验证

创建测试环境后执行：

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e './gzh[test]'
.venv/bin/python scripts/validate_repository.py
.venv/bin/python scripts/plugin_check.py
.venv/bin/python scripts/release_check.py --mode source-only
.venv/bin/python -m pytest -q gzh/tests tests
.venv/bin/python scripts/eval_runner.py static
.venv/bin/python scripts/gentoo_integration.py --validate-only
.venv/bin/python .agents/skills/gentoo-overlay-development/scripts/source_manager.py \
  audit --all-scopes --fail-on-drift
```

验证器检查 skill frontmatter、metadata、activation eval、禁止的非 Latin script，以及来源 schema 和 lock。它也检查局部链接、reference 可发现性、可执行脚本和 plugin package。英文政策仍须人工复核，因为验证器不能把 Latin 字符文本自动判定为英语。

验证器还把每个 skill 的 `name`、`description` 和解析后的 `SKILL.md` 路径序列化为确定性的初始列表估算值，并以官方文档给出的 8,000 字符上限作为硬上界。该值用于保守检测，不声称复制客户端内部的序列化格式。客户端仍可因实际 context budget 提前缩短 description。

`.github/workflows/gentoo-integration.yml` 在相关验证文件 push 或人工 dispatch 时，使用固定 digest 的官方 Gentoo amd64 stage3。有界 runner 要求初始环境没有 Gentoo ebuild repository 和 Git。runner 随后使用 `emerge-webrsync` 获取并验证当前的官方 repository snapshot，再以最小 USE 状态从 source 安装 stage3 未包含的 Git。

Git bootstrap 明确禁用 binary package，避免 rolling binary host 的 build variant 改变测试基础。`bootstrap.json` 记录签名 `Manifest` 的 SHA-256 与时间戳、`timestamp.commit` revision、Git PF、USE、`REPO_REVISIONS` 和 ebuild SHA-256。这些字段标识本次使用的滚动 snapshot，不把它表述为由 stage3 digest 固定的输入。

命令失败时，workflow 仍上传报告、命令输出及其 SHA-256。如果输出路径已存在，runner 会拒绝写入，避免覆盖既有证据。

workflow 执行两个 EAPI 8 source-merge fixture。正常 fixture 必须产生 VDB、预期 artifact 且没有保存的 elog；边界 fixture 必须在 emerge 成功后因 `qa` elog 被拒绝。workflow 还在隔离 Git 副本中直接执行正式 `run_verify_install`，并要求两个 fixture 得出相同的接受或拒绝结果。该验证不代表 overlay、profile 或 architecture matrix；真实 package 仍须按目标 repository 的实时政策执行 `pkgcheck`、构建、安装和 CI。

静态 eval 当前覆盖 85 个 activation、exclusion 和行为案例。外部 runner 通过显式 JSON protocol 接入，不会把预期答案写入待测 prompt。安装测试只写入临时目录。

## 目录

- [`.agents/skills`](.agents/skills)：可安装 skill；
- [`.agents/.codex-plugin`](.agents/.codex-plugin)：Codex plugin manifest；
- [`.claude-plugin`](.claude-plugin)：Claude Code marketplace；
- [`gzh`](gzh)：Python CLI 与单元测试；
- [`scripts`](scripts)：安装、更新和仓库验证；
- [`docs/devmanual.md`](docs/devmanual.md)：本项目使用的 Gentoo 官方文档索引；
- [`RELEASING.md`](RELEASING.md)：发布身份、验证门、产物边界和后续事项；
- [`AGENTS.md`](AGENTS.md)：本仓库维护约定。

`docs/superpowers` 保存早期设计和实施记录，只用于历史背景。运行时合同由目标仓库政策、当前 skill、确定性测试和目标仓库 CI 共同确定。
