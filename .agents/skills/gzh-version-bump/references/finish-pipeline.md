# 收尾流程（执行器，所有硬门必须过）

阶段 A 产出"改好的新 ebuild"后，依次：

1. **校验（🔴硬门）**：`gzh lint <new_ebuild>`——devmanual 规则 + ~arch。Error 即停。
2. **Manifest（🔴硬门）**：`gzh manifest <new_ebuild>`——fetch distfiles + checksums。fetch 失败即停（检查 SRC_URI）。
   - **截断门**：fetch 成功 ≠ 完整。`gzh manifest` 底层调 `pkgdev manifest --force`，会对被截断的 distfile 照样算哈希写进 Manifest；本地 install 通过（比对的是同一个截断文件），CI 独立重取完整字节时 VERIFY FAILED。对偏大的 distfile（约 ≥50MiB，越大越要查）核对本地文件大小 vs 上游 asset 大小（github 用 `gh api repos/<O>/<N>/releases/tags/<T> --jq '.assets[].size'`，其它源比对 HTTP Content-Length）；不符即 `rm` 该 distfile + 删 Manifest 对应 DIST 行，`curl -L -C - --retry 3` 断点续传后重跑 `gzh manifest`。
3. **QA（🔴硬门 Error）**：`gzh pkgcheck <pkg_dir> --min-severity error`——Error 必须清零；Warning 记录不阻断。
   - 注：`gzh pkgcheck` 跑的是离线 `pkgcheck scan`（无 `--net`/`--commits`），不覆盖网络类检查（SRC_URI 的 DeadUrl/RedirectedUrl）；overlay 包不在 Gentoo 官方镜像上，死 SRC_URI = 真不可 fetch，开 PR 前仍须跑 `pkgcheck scan --commits --net`（见步骤 7），本步不能替代。
4. **编译验证（🟡软门）**：`gzh build-test <new_ebuild>`（**默认 full**：clean→install 完整编译验证）。只有明确理由才降级：`--level quick`（仅到 configure；包巨大或 full 受限时）或 `--level none`（**必须**在交付报告注明跳过原因）。失败→提示转 fix-build-failure。注：非 root 环境下 install phase 会因 portage chown 失败，完整 full 需在 root/chroot 下跑。
   - **CI elog 硬门复现（build-test 只看 returncode、`gzh pkgcheck` 是静态扫描，两者都不读 Portage elog——而 CI 恰恰在 elog 上判红，这一步补上）**：full build-test 过后照 CI 复现 elog 门。用解析依赖的 emerge，设 `PORTAGE_ELOG_CLASSES="qa warn error" PORTAGE_ELOG_SYSTEM="save" PORTAGE_LOGDIR="$LOGDIR"`（PORTAGE_LOGDIR 默认 /var/log/portage；elog `save` 模块把文件写进 `$PORTAGE_LOGDIR/elog`，不设时该 elog 目录默认 /var/log/portage/elog，CI 即用此默认并扫 `/var/log/portage/elog/*`），然后扫 **`$LOGDIR/elog` 下的 elog 文件**（非 emerge stdout；`--quiet` 会把 qa-class 如 go.mod 'update BDEPEND' 压掉 console 却仍存 elog）。最忠实于 CI 的复现是照 CI 的顺序：`emerge --onlydeps <目标>` → `rm -rf "$LOGDIR/elog"/*`（清掉依赖产生的 elog）→ `emerge <目标>` → 目录里任一 elog 文件即 CI 会红；这样无需假设 elog 文件名格式。若不清 onlydeps 而想按包过滤，elog 文件名是 `<category>:<PF>:<time>.log`（PF 含 -rN 修订），故 glob 用 `<cat>:<PF>:*`。区分包类型：`-bin`/预编译包 `ebuild <eb> clean install` 够用、扫 stdout 'QA Notice' 但忽略 'Unresolved soname'（RDEPEND 未装的误报，留给 emerge 门）；源码包的 elog 门只有靠解析依赖 emerge 才成立，bare `ebuild install` 不解析 DEPEND。良性（soname 未解析 / CONFIG_CHECK 无内核 / binchecks）注明放行；真问题（rpm_unpack / pre-stripped / desktop-file-validate）改 ebuild。
5. **变更摘要**：`gzh diff-ebuild <old_ebuild> <new_ebuild>`，总结改动与理由。
6. **提交**：`gzh commit <new_ebuild> <Manifest> [-m "category/package: add <PV>"]`（默认不 drop）。commit 无 AI 署名。
7. **交付**：默认停在此（本地分支 + commit）。
   - **开 PR 前 URL 硬门（🔴，仅在要开 PR 时跑）**：`gzh pkgcheck` 跑离线 `pkgcheck scan`（无 `--net`/`--commits`，见步骤 3），不查 SRC_URI 的 DeadUrl/RedirectedUrl；overlay 包不在 Gentoo 官方镜像上，死 SRC_URI = 真不可 fetch，故开 PR 前须用**原生 pkgcheck**（不是 gzh 包装——`gzh pkgcheck` 无 `--net`）在 pkg 目录跑 `pkgcheck scan --commits --net`。命中 github 多为 rate-limit 假阳性——先过滤 `attr==SRC_URI`（HOMEPAGE 的 DeadUrl 不阻断安装，跳过），对每条命中用 `curl -r 0-0 -A '<浏览器 UA>' --retry 2 -L` 逐条复核，仅非 2xx 才算真死；403 多为 auth-gated / RESTRICT=fetch 设计如此，交人工。
   - **URL 门过后再开 PR**（显式）：`gh pr create --repo gentoo-zh/overlay --base master --head $(gh api user --jq .login):<branch>`（head 动态取，不写死）。

**重试上限 3 次**：任一硬门/编译同一错误重复 2 次即停，报告失败步骤+错误+每次尝试，询问继续/跳过/放弃。
