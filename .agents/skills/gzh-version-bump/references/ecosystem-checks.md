# 生态专项 bump 检查清单

按上游生态分组的升版本 QA 检查，供 A5（依赖评估）/A6（patch 评估）按包类型对照，也可在收尾 build-test 红时倒查。每条附 gentoo.git 证据 commit（provenance，非本 overlay 提交）。命中不一定阻断，但要么改 ebuild、要么在交付报告注明。工具优先：能用 `gzh ebuild-parse` 或解压源码后 grep 判定的，别凭记忆猜。

## 编译型（c/c++、rust、go、eclass 迁移）

- **CMake 4 最低版本回退** — 解压源码 grep `cmake_minimum_required`（含 `*.cmake`），标出 < 3.5 的可构建子树。cmake-4 直接报 'Compatibility with CMake < 3.5 has been removed' 拒绝 configure：上版本能编、bump 后 configure 就挂。修：改 range 形式或删该子树。 _[b8bff05]_
- **构建期联网拉取（FetchContent/ExternalProject）** — grep `FetchContent_Declare|ExternalProject_Add` 的 GIT_REPOSITORY/URL，未被 ebuild 覆盖（`-DFETCHCONTENT_SOURCE_DIR_*`、SOURCE_DIR patch、`DOWNLOAD_COMMAND true`）即违规。network sandbox 下构建期下载硬失败，新版常悄悄以此 vendor 新依赖。 _[0e9c7e1]_
- **强塞全局优化/加固/LTO flag** — grep meson.build 的 `add_global_arguments|add_project_arguments`、Makefile.in/configure 里 `-flto -D_FORTIFY_SOURCE -fstack-protector -O[0-9s]`；未打 patch 的命中即违规（需 respect-flags patch 删掉）。上游硬加会盖过发行版工具链加固和用户 flag，新版会重新引入。 _[e6dc5e5]_
- **mycmakeargs 拼错 / configure 选项改名（USE 静默失效）** — 解析 `mycmakeargs=(...)`，非 `^-D`/`^--`/`$(...)` 的字面量被静默吞；每个 `$(use_enable flag opt)`/`use_with` 的 opt 对照解压后 `configure --help` 是否仍存在。少个 -D 或选项改名让 USE flag 变空操作，功能悄悄不再编入。 _[98e55e6, ad78494]_
- **新工具链缺 #include** — 编译失败看 'X was not declared' / 'PATH_MAX undeclared' / '(u)intNN_t undeclared'；修法是最小 include-only patch（`<cstdint>`/`<climits>`/`<cstring>`），绝不回退旧编译器。新工具链/新依赖丢掉了传递提供的头，bump 前能编的挂了。 _[f92eebc, e28a4db]_
- **patch 动了 autoconf 输入却没 eautoreconf** — PATCHES/eapply 改了 configure.ac 或 m4/*.m4，但 ebuild 既没 inherit autotools 也没调 eautoreconf = 空操作 patch。只改 Makefile.in（配同 hunk 的 Makefile.am）可避免拉入 eautoreconf。bump 后 rebase 的 patch 悄悄啥也没干，预期修复缺失。 _[ea0bccb]_
- **bump 后残留/未引用的 files/ patch** — 每个 files/*.patch 都要被某 ebuild 引用、每条 PATCHES 都要解析到实存文件；rename-only（100% 相似）的 ebuild bump + 新版本后缀 patch 但 PATCHES 仍不指向它 = 几乎必错。bump 若还指旧 patch（或丢了携带的下游 patch），装出来是坏的或丢了修复。 _[bd3a045, f6f229a]_
- **go.mod toolchain 下限 vs BDEPEND** — 取新版 go.mod 的 `go X.Y.Z`（及 `toolchain`），对比 BDEPEND 里最高 `>=dev-lang/go-*`；go.mod > 下限（或无 go atom）即违规。eclass 不强制该下限，bump 到需要更新 Go 的代码会给用户莫名编译错。 _[bbdf70d]_
- **RUST_MIN_VER vs Cargo.toml rust-version** — 取解压后 Cargo.toml 的 rust-version（workspace 成员 + vendored crate 取最大）对比 ebuild 的 RUST_MIN_VER；Cargo.toml > RUST_MIN_VER（或 cargo ebuild 缺 RUST_MIN_VER）即违规。MSRV 抬升没同步会让老 Rust 上真编译失败。 _[6ca2ba0]_
- **跨 bump 复用旧 deps/vendor tarball** — SRC_URI 里 `*-deps.tar.*`/`*-vendor.tar.*` 内嵌版本 ≠ ${PV} 即违规（旧 vendor/GOMODCACHE tarball 被套给新版）。vendor tarball 必须精确匹配新版依赖集，套旧的给出错或缺 crate/module。→ 重新生成 `${P}-vendor.tar.xz`。 _[0ea353d, eb616d7]_
- **Go/Rust link flag 里预 strip（-s）** — grep ebuild 及所打 patch 的 `-ldflags` 串里的 `-s`（或 `ldflags.*"-s"`）；patch 掉 `-s`（及散落的 `-w`）让 portage 控制 strip、splitdebug 生效，保留 `-X` 注版本 flag。预 strip 的 Go/Rust 二进制触发 strip/splitdebug QA。 _[6b2d63e]_
- **crates.io 旧 API 下载 URL** — grep `crates.io/api/v1/crates`（ebuild/eclass），命中即违规。改用 `https://static.crates.io/crates/<name>/<name>-<version>.crate`（CDN，无需箭头 rename）。api/v1 封禁无 UA 抓取，CRATES 列表 bump 刷新时 crate distfile 下载失败。 _[3929f73]_
- **inherit 了 @DEAD/@DEPRECATED eclass** — 逐条 inherit 去 grep 其 ::gentoo 头 `^# @DEAD`/`^# @DEPRECATED:`，命中即违规（报继任者：qmake-utils→qt-utils、linux-mod→linux-mod-r1、bash-completion-r1→…）。::gentoo 一删该 eclass，overlay 就 'unknown eclass'，先迁再说。 _[b7c38ce, 0db7dda]_
- **EAPI 9 禁用的 eclass API** — grep `\b(epatch|epause|ebeep|versionator|user_add)\b`、`(?<!get_)makeopts_(jobs|loadavg)`、EAPI=9 下带位置参数的 make_desktop_entry（需 -n/-i）；USE 条件分支也要扫。EAPI 9 下运行时 die，bump EAPI 没扫全每条分支就硬失败。 _[90e9f9f, 86137a6, 8202bb4]_

## 脚本生态（python / node·-bin / java / perl）

- **PYTHON_COMPAT 含死实现** — grep PYTHON_COMPAT 里 python3_11 及更老、无版 pypy3、pypy3_11、python3_13t 等 EOL/退役项。EAPI 7/8 静默忽略、EAPI 9 die；COMPAT bump 要滑窗并删 EOL 实现。 _[5d00c0a, 545def9]_
- **PEP517 后端别名（flit→flit-core）** — grep `DISTUTILS_USE_PEP517=(flit|poetry|flit_scm|jupyter)` 精确匹配（非 -core），对照 sdist pyproject.toml 的 build-backend。短别名会把整套前端工具拉进 BDEPEND，EAPI 9 下致命；backend 可能变，每次 bump 复检。 _[be58ce7, 658a4a9]_
- **python_gen_cond_dep 实现列表过期** — 每个 python_gen_cond_dep 的硬编码实现列表/模式对照 PYTHON_COMPAT：缺最新 COMPAT 成员会静默丢该依赖；COMPAT 里无 pypy 的 `python*` 模式应改无条件。列表不随 COMPAT 增长，bump 后新解释器上依赖悄悄消失。 _[be92bfa, b72dd75]_
- **EPYTEST_* 写在 distutils_enable_tests 之后** — 行序解析，任何 `EPYTEST_(PLUGINS|RERUNS|TIMEOUT|XDIST)=` 出现在 distutils_enable_tests 之后即错。该函数调用时就把这些旋钮转成 test dep，之后再设会静默丢掉 plugin/rerun BDEPEND，测试因缺依赖失败。 _[450f590]_
- **pytest 缺 hermetic plugin 列表** — 有 `distutils_enable_tests pytest` 却全 ebuild 无 `EPYTEST_PLUGINS=`（空数组 `()` 才是正确 opt-out）。无显式列表 pytest 自动加载系统 plugin，bump 时非确定性/伪测试失败。 _[d0452a5]_
- **预编译包 KEYWORDS 未锚 -*** — ebuild 设 QA_PREBUILT 或装 /opt 下二进制 distfile，但 KEYWORDS 未以 `-*` 开头；也 `grep -L QA_PREBUILT` 扫 *-bin。**策略（已采纳）**：私有预编译 blob 用 `KEYWORDS="-* <只列上游确实发布二进制的 arch>"`，每 arch 配自己的条件 SRC_URI 块。缺 `-*` 暗示了二进制没有的可移植性，在其它 keyword arch 上会坏。 _[cfc72a0, 255becc]_
- **单 arch SRC_URI 无 arch 条件** — SRC_URI 含 arch 标记名（linux_amd64/x86_64/arm64/aarch64）却无对应 `amd64?( )`/`arm64?( )` 块、各自 Manifest 条目。无条件的 arch 专属 fetch 在别的 keyword arch 上取到错/缺的产物，bump 对那些 arch 就坏。 _[4fd355e]_
- **soname 与 flags 是两套 QA，别当成一回事** — 扫 `-bin` ebuild 里想压 unresolved-soname 的写法：**没有变量能真正屏蔽 soname 检查**（`QA_PREBUILT="*"` 也不行），这类赋值只掩盖 bundled 组件解析不了私有库；正解 `patchelf --set-rpath '$ORIGIN/...'` 加上对真系统库的真 RDEPEND，详见 [prebuilt-qa.md](prebuilt-qa.md)。`QA_FLAGS_IGNORED` 不属这一类：它只被 `install-qa-check.d/10ignored-flags` 读，对应 Files built without respecting CFLAGS/LDFLAGS。因为 CFLAGS 那半要求 CFLAGS/CXXFLAGS/FFLAGS/FCFLAGS 全带 `-frecord-gcc-switches`、LDFLAGS 那半要求 LDFLAGS 带 `--defsym=__gentoo_check_ldflags__`（只有 developer profile 有），而 CI 的 stage3 是 desktop profile，所以这道门在 CI 上不触发：cargo ebuild 里已有的 `QA_FLAGS_IGNORED` 是上游惯例（rustc 不把 LDFLAGS 交给链接器），照留不动、也别当坏味道删，同样别为过 CI 新加。Go 一般不需要，静态二进制没有 `.dynsym`，这条检查直接跳过。 _[13a7a9d, f9a2b99]_
- **rpm.eclass payload 类型不符** — 对比声明的 RPM_COMPRESS_TYPE 与 `strings <rpm> | grep -o 'PayloadIs[a-zA-Z]*'`；不符/错类型 unpack 失败（lzma payload 只有 rpm2targz 可用即硬错）。RPM_COMPRESS_TYPE 是 @PRE_INHERIT，须在 `inherit rpm` 之前设。 _[035e309]_
- **java：硬编码 --add-opens 无 JVM 探测** — grep JAVA_TEST_EXTRA_ARGS 的 `--add-opens`，同 ebuild 内无 `java-config -g PROVIDES_VERSION`/ver_test 探测即错。JDK 17+ 反射 flag 要按活动 JVM 版本 gate，硬编码在老 JVM 上坏或误导。 _[f93b836, 34e121c]_
- **perl：DIST_VERSION / PV 规范化不符** — 对每个 dev-perl ebuild 套 eclass 的 CPAN float→dotted 规范化，要求 `normalize(DIST_VERSION) == PV`；PV 无法 round-trip 又缺 DIST_VERSION 的，SRC_URI 是坏的。CPAN float（0.08→0.80.0）不匹配 tarball 名，假设 PV==tarball 会 fetch 不存在的 distfile。 _[aa70e3d, e58a386]_
- **perl：ParseXS 3.58（perl 5.44）下 XS 编译坏** — 解压源码 grep .xs：`^PROTOTYPES:` 后非 ENABLE/DISABLE（如 DISABLES 拼错）；`^MODULE *=` 紧接（无空行）以 `#` 开头的行；XSUB 签名里 `length([A-Za-z_]` 伪参数。ParseXS ≥3.58 这些变硬错，perl bump 后不编译。分别修：改关键字 / MODULE 与指令间插空行 / 改回普通 SV*。 _[3d11ab4, d9a451a, 50626fc]_

## 依赖正确性与卫生

- **pkg-config 宏但 BDEPEND 缺 virtual/pkgconfig** — 源码含 `PKG_CHECK_MODULES`/`pkg_check_modules`/meson `dependency()`，但 ebuild BDEPEND 无 virtual/pkgconfig。只在干净/最小构建机上暴露，bump 可能新引入未声明的 pkgconfig 依赖。 _[799149d]_
- **裸 dev-python 依赖缺 usedep** — inherit python-r1/python-single-r1/distutils-r1 的 ebuild 里 `dev-python/<pkg>` atom 缺 `[${PYTHON_USEDEP}]`/`[${PYTHON_SINGLE_USEDEP}]`。裸 atom 可能被错解释器满足，运行时 import 失败；修需 revbump。 _[bd20876]_
- **import pkg_resources 无对应依赖** — 源码 grep `import pkg_resources`/`from pkg_resources` 但 ebuild 无 dev-python/pkg-resources 依赖。新版 setuptools 不再带 pkg_resources，bump 后运行时/测试 ModuleNotFoundError。 _[c4cda74, 846bb49]_
- **env 条件的 metadata** — grep 全局作用域里测环境状态（is-java-strict、`[[ -n ${JAVA_PKG_*} ]]`）的 DEPEND/BDEPEND 赋值。metadata 被缓存、必须是 ebuild+eclass 文本的纯函数；env 条件依赖给出非确定性、错误的依赖解析。 _[3a2e787]_
- **USE 条件依赖的目标已消失** — 对 PDEPEND/RDEPEND 里每个 `flag?( cat/pkg:slot )` 核对目标 package:slot 仍在树里；lockstep 家族（perl virtual/*、java 库）bump 后对每个 `>=`/`=` sibling 依赖解析（`${PV}` 快捷写法最易出坑，上游可能跳过某 sibling 的一个 release）。last-rite 的目标留下不可满足 atom。 _[df39073, 82b7f1a]_

## 上游位置与内核·模块

- **上游位置过期（重定向）** — HEAD 每个 SRC_URI/HOMEPAGE；永久重定向（301/308）落到不同 host 或不同 forge org/repo 路径即违规。bump 时 fetch 可能只靠上游随时可撤的重定向；要同步更新 HOMEPAGE/SRC_URI/metadata.xml remote-id/nvchecker URL。 _[6b05342, ee96ffc]_
- **remote-id 与 SRC_URI org/repo 不符** — 解析 metadata.xml remote-id（github/gitlab/codeberg）的 org/repo，对比 SRC_URI/HOMEPAGE 的 org/repo，不符即违规。仓库搬家后 remote-id 指向死地址，bump 时先于 nvchecker 抓到半成品搬迁。 _[ee96ffc]_
- **forge URL 路径里用 ${PN}** — grep SRC_URI/HOMEPAGE 的 `github.com/[^/]*/${PN}` 或任何 org/repo 段用 ${PN}，命中即违规。仓库名必须字面写死；PN 一旦与上游仓库名分歧（rename/bump 后常见）就静默坏。 _[2759f03]_
- **不稳定动态归档 URL** — grep SRC_URI 的 `/-/archive/`、`;a=snapshot`、cgit `/snapshot/`，命中即违规。这类即时生成的归档无固定 checksum/可用性保证，bump 用它得到的 distfile 会变或消失。 _[8c84cc6, 6d5687e]_
- **commit-hash 归档缺 S 覆盖** — SRC_URI 匹配 `/archive/[0-9a-f]{40}` 却无引用 hash 变量的 `S=` 即违规。forge 的 commit 归档解到 `${PN}-${COMMIT}` 而非 ${P}，快照 bump 忘了 S= 会找不到源码目录。 _[14e0c3c, 5c64ede]_
- **仍 inherit 已删的 linux-mod** — `grep -rE 'inherit.*\blinux-mod\b'` 排除 linux-mod-r1，命中现在就坏。linux-mod.eclass 已从 ::gentoo 删除，out-of-tree 模块 ebuild 仍 inherit 就 unknown-eclass；bump 必须迁到 linux-mod-r1（且 pkg_postinst/src_compile/src_install override 要调对应 `linux-mod-r1_<phase>`）。 _[39b122a]_
- **模块内核上限/依赖滞后** — MODULES_KERNEL_MAX 对比树里最高 sys-kernel/gentoo-kernel 分支，天花板低于它即标记（抬前先 build-test）；内核 provider 须 `PDEPEND` on `>=virtual/dist-kernel-${PV}`（别用派生变量、别放 RDEPEND/DEPEND，否则 virtual 落后于已装内核）。 _[7f7461b, cc1f450]_
- **新加 ebuild 带稳定 keyword** — 对每个 `git diff --diff-filter=A` 新增的 *.ebuild，标出不以 `~` 或 `-` 开头的 KEYWORDS token。为 bump 复制 ebuild 会带过来稳定 keyword，等于把未测新版直接 stable；overlay 新增 ebuild 应 `~arch`。 _[c548fa4]_

## 源码包的 elog 分类（补 [finish-pipeline.md](finish-pipeline.md) 步骤 4）

那里和 [prebuilt-qa.md](prebuilt-qa.md) 第 6 节列的是预编译路径的 triage。源码包另有几类，都出自 portage 扫构建日志，离线 pkgcheck 一条都看不出来：

- `QA Notice: setuptools warnings detected` — portage 匹配日志里 `.../setuptools/...: *Warning: ` 的行（`Normalizing ...` 与 `setup.py install is deprecated` 已内建忽略）。在 `python_prepare_all` 里改源：`License ::` trove classifier 常是跨行拼接，单行 sed 匹配不到、要整块删；`[project] license = { file = ... }` 改成 PEP 639 字符串。不 merge 也能复现：`gpep517 build-wheel --backend <backend> --wheel-dir <dir> --output-fd 1`，看 stderr。
- `QA Notice: Unrecognized configure options` — 就是上面那条 `use_enable`/`use_with` 选项改名在 emerge 期现形：那个 USE flag 现在是空操作。照解压后 `configure --help` 改对，不按良性放行。
- `QA Notice: Found the following implicit function declarations in configure logs` — 逐个符号判：目标平台上确实不存在的（其它 OS 的 API、已删的调用）写进 `QA_CONFIG_IMPL_DECL_SKIP=( sym )`，每个符号配一行注释；其余是真缺 include 或真缺依赖探测，要修。
- `QA Notice: Package triggers severe warnings ...` — 因为 portage 是拿 `warning: ` 文本去 grep 构建日志（`install-qa-check.d/90gcc-warnings` 的 pattern 表），所以 `-Wno-error=` 没用，得让警告本身消失：用 include-only 的 `files/*.patch`；只有 C23 关键字那类（`bool`/`true`/`false` 当标识符）可以 `-std=gnu17`。

elog 会叠加，一次扫到的全部修完再收工。CI 只 emerge 本次改动的包、树里旧版本不重编，所以同类问题在旧版本上不会红，别顺手改。判环境假阳性先做基线对照：同条件构建改动前那版，一样红就不是本次引入，注明放行别改 ebuild。
