# 预编译二进制包 QA（prebuilt blob）

预编译 `.deb`/`.rpm` blob（Electron 应用、插件宿主大应用、含 Node 原生 addon 的应用）常常本地 install 干净、却在 overlay 的 emerge-on-pr CI 上因 `QA Notice: Unresolved soname dependencies` 变红。注意 `gzh build-test` 只跑 ebuild phase、**不读 elog**，所以预编译包必须额外手动抓 elog 才算验证到位。本文是 `-bin`/预编译路径的 QA 清单，配合 overlay `AGENTS.md` 的 “Bundled and Prebuilt Binaries” 与 [finish-pipeline.md](finish-pipeline.md)。

## 1. KEYWORDS / SRC_URI

- 只 keyword 上游**实际发布了二进制**的 arch，每个 arch 用独立的 USE-conditional `SRC_URI` 块。
- 每 arch 私有的 proprietary blob 推荐 `KEYWORDS="-* <上游发布二进制的 arch>"`（如 `-* ~amd64 ~arm64`）——`-*` 明确它只在这些 arch 有 blob，别的 arch 拿不到文件。
- proprietary / 限制再分发的 blob：用命名的 `licenses/<Name>` 文件 + `RESTRICT="mirror"`（禁止再分发再加 `bindist`），不镜像。

## 2. 声明与 eclass

- `QA_PREBUILT` 只用于人工审过的不可变上游 blob，值写精确安装路径（`opt/${PN}/*`、`usr/bin/${MY_PN}`），不要写 `*`：因为它会一次展开进 `QA_DT_NEEDED`/`QA_SONAME`/`QA_SONAME_NO_SYMLINK`/`QA_PRESTRIPPED`/`QA_EXECSTACK`/`QA_TEXTRELS`/`QA_WX_LOAD`/`QA_FLAGS_IGNORED`，所以 `*` 等于把整包这些检查全关掉。overlay 里存量的 `QA_PREBUILT="*"` 是历史写法，别照抄。
- 预剥离过的 ELF 加 `RESTRICT="strip"`：挡住 portage 再 strip 的是 `RESTRICT`，不是 `QA_PREBUILT`。`splitdebug` 是另一件事，只在 debug 分离对 blob 失败时才加。
- rpm blob：`RPM_COMPRESS_TYPE` **必须在 `inherit rpm` 之前**设（它是 `@PRE_INHERIT` 变量）——gzip/zlib payload 用 `"none"`，zstd payload 用 `"zstd"`；设晚了 `rpm_unpack` 会解不开。

## 3. Unresolved soname 三路排查（真修，不糊）

关键认知：`QA_PREBUILT` 对这条检查无效——它喂的是 `install-qa-check.d` 里缺 SONAME、缺 DT_NEEDED 那几个检查，以及 `Unrecognized ELF file(s)` 的白名单；而这条 elog 出自 `FEATURES=qa-unresolved-soname-deps`（默认开），拿本包 build-info 的 `REQUIRES` 比对已装包的 `PROVIDES` 得出，唯一能消音的是 `REQUIRES_EXCLUDE`，而 overlay AGENTS.md 禁止拿它消音。原则：**不能为过 QA 而糊 QA，库必须真解析**。portage 的静态检查对每个 ELF **孤立**解析 `DT_NEEDED`，够不到兄弟目录里的库就报——这正是 CI 的失败集。三种修法按 elog 里 soname 的类型对号入座：

**(1) RPATH reach** —— 把库路径加回 ELF 的 RPATH，让它够得到同包 blob 里的库。对 blob 下每个 ELF：

- 门：`patchelf --print-rpath "$f" 2>/dev/null || continue`（跳过带 `+x` 的非 ELF 脚本/数据）。
- **简单应用 / Electron**（addon 不被 dlopen 进全局符号域）：`patchelf --set-rpath '$ORIGIN:$ORIGIN/..[:...]' "$f"`——写 `DT_RUNPATH` 即可。
- **插件宿主应用**（核心库预加载进全局符号域、plugin/addon 运行时才 dlopen，其 `DT_NEEDED` 指向自身 RPATH 够不到的目录）：`patchelf --force-rpath --set-rpath '$ORIGIN:...' "$f"`——`--force-rpath` 写的是 `DT_RPATH`（可被子对象继承），dlopen 出来的 addon 才解析得到；只用 `--set-rpath` 写的 `DT_RUNPATH` **不继承**，插件宿主会漏。
- reach 路径 = 该文件净化后的自身 RUNPATH + `$ORIGIN` + `$ORIGIN/..` + 到 blob 根目录的 `$ORIGIN`-相对路径 + 下面 ldd 扫出来的跨插件库目录；相对深度可用 `tr -cd '/' <<< "$reldir" | wc -c` 算。加 reach 直到 unresolved 归零。

**(2) `patchelf --replace-needed`** —— Debian 专有 / 无版本 soname 改名到 Gentoo 提供的名字：

- `patchelf --replace-needed libbz2.so.1.0 libbz2.so.1 "$f"`（Debian 的 `.1.0` → Gentoo 的 `.1`）。
- `patchelf --replace-needed libc++.so libc++.so.1 "$f"`（bundled 的 libicu*/libv8* 链接了**无版本**的 `libc++.so`，而 blob 只提供 `libc++.so.1`）。

**(3) 删组件 / 删非 host addon** ——

- 依赖 Gentoo 上根本不存在的库的组件永远跑不起来，直接删掉。
- Node 原生 addon（koffi FFI、sharp/@img 之类）blob 里往往打包了多 os/arch 的预编译产物；`src_install` 只留 host 这一份、`rm -rf` 其它，否则非 host 那些会触发 unresolved-soname。

**RDEPEND**：只对**真正被系统链接**的 NEEDED soname 加 RDEPEND；bundled 进 blob 的那些不加。

## 4. 真验证（两个真相都要过）

- **CI 真相（elog）**：`gzh build-test` 不读 elog，手动跑一次真 emerge 抓——`PORTAGE_ELOG_CLASSES="qa warn error" PORTAGE_ELOG_SYSTEM=save`（两个**一起**设：只设 `save` 不设 `PORTAGE_ELOG_CLASSES` 抓不到 QA notice），扫 elog 文件 = 0 saved elog 才算过。elog 列出的就是 CI 会红的确切集合。
- **运行时真相（ldd）**：`ebuild <eb> clean install` 后，对 image 里每个 `.so` 跑 `ldd`（**不带 LD_LIBRARY_PATH**）→ 0 个 `not found`。portage 静态检查会把“同包内另一 ELF 按 SONAME 提供”的库算作已解析，但跨插件引用（addon A 需要 addon B 的库）能过 QA 却仍在运行时炸——靠这个 ldd 扫补上。
- 循环 `emerge → 修 → 再扫`，直到 elog 空且 ldd 干净。install phase 会因 portage chown 需要 root/chroot。

## 5. 其它反复踩的 QA

- **.desktop（desktop-file-validate 门）**：删掉废弃的 freedesktop category（如 `Application`）；加主 category（如 `Office`）**前先看** blob 里的 `.desktop` 是否已经有——已有还加 → `X more than once` 红 CI，只在缺失的版本加、别重复。每次 bump 重新核对安装的 `.desktop` **basename**（升级可能把它改成 app-id 命名如 `com.vendor.App.desktop`，`domenu` 的引用要跟着改）。
- **exec 位**：`doins` 装成 0644，会把 helper 二进制的 `+x` 剥掉；用 `find <blobdir> -type f -perm -u+x` 找全再 `fperms 0755`，别只硬写那几个。
- **Electron flag**：不要把 GPU/ozone/wayland flag 焊进 Electron 的 `Exec=`（GPU 崩溃多是环境问题、非打包 bug）；出上游默认 `Exec`，让用户自己加（照 google-chrome）。
- **edition/locale 陷阱（中文 overlay 尤其）**：bump 前查 bundled 的 locale/语言目录里有没有 `zh_CN`，确认新版没换 edition、没丢语言——官方“国际版” blob 可能只有 en/... 不含 `zh_CN`，盲升会把中文用户的语言弄没。

## 6. CI elog triage（红了先分良性 / 真问题）

- **良性**（注明放行、不改 ebuild，但要在 PR body 里点名让人可 merge）：unresolved-soname 在其 RDEPEND provider 装上后就解（CI 里缺 provider 的误报）、`CONFIG_CHECK` / `Unable to check kernel config`（CI 容器无内核）、kernel-source 包的 `RESTRICT=binchecks`。
- **真问题**（改 ebuild）：`rpm_unpack` 失败、`Pre-stripped files found`、`desktop-file-validate` 报错。

## 7. 解包大 blob & 截断 distfile

- 解 `.deb`/`.rpm` 到**磁盘**临时目录（`/var/tmp` 或 `$PORTAGE_TMPDIR`，**不要 tmpfs**——几 GB 的树会撑爆 tmpfs 卡死 shell）：`.deb` 用 `ar x` + `tar xf`，`.rpm` 用 `rpm2cpio | cpio -idm`。
- fetch 成功 ≠ 完整：偏大的 distfile 核对本地文件大小 vs 上游 asset 大小（github 用 `gh api repos/<O>/<N>/releases/tags/<T> --jq '.assets[].size'`，其它源比 HTTP `Content-Length`），不符即 `rm` 该 distfile + 删 Manifest 对应 `DIST` 行、`curl -L -C - --retry 3` 续传后重跑 `gzh manifest`（详见 [finish-pipeline.md](finish-pipeline.md) 步骤 2）。

## 8. 多 arch

patchelf / `--replace-needed` / 按文件名删组件都是**按名字**操作、arch 无关，同一套 `src_install` 代码在各 arch 复用，多 arch ebuild 只需相同代码 + 对应 `KEYWORDS` + per-arch `SRC_URI`。CI 除两个 amd64 profile 外，对 `KEYWORDS` 含 `arm64`/`~arm64` 的包还会在 `ubuntu-24.04-arm` 上以 `arm64-desktop-systemd` 真编、elog 门照判，所以上游发布了 arm64 产物就加 `~arm64`，没有 arm 机器也加、由 CI 那条腿去验；但它不是免费的：blob 缺 arm64 组件、patchelf 漏改那一格照样红，出问题按报告修。手上有 arm 设备的自己先验。本机没跑过的 arch 静态核验即可（patchelf 读跨 arch 的 ELF 头不需执行，只有 arch loader 会显示为 external，不算真 unresolved）。多 arch 未在本机跑过的开 draft PR、标注“未测”。
