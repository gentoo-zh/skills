# 收尾流程（执行器，所有硬门必须过）

阶段 A 产出"改好的新 ebuild"后，依次：

1. **校验（🔴硬门）**：`gzh lint <new_ebuild>`——devmanual 规则 + ~arch。Error 即停。
2. **Manifest（🔴硬门）**：`gzh manifest <new_ebuild>`——fetch distfiles + checksums。fetch 失败即停（检查 SRC_URI）。
3. **QA（🔴硬门 Error）**：`gzh pkgcheck <pkg_dir> --min-severity error`——Error 必须清零；Warning 记录不阻断。
4. **编译验证（🟡软门）**：`gzh build-test <new_ebuild>`（**默认 full**：clean→install 完整编译验证）。只有明确理由才降级：`--level quick`（仅到 configure；包巨大或 full 受限时）或 `--level none`（**必须**在交付报告注明跳过原因）。失败→提示转 fix-build-failure。注：非 root 环境下 install phase 会因 portage chown 失败，完整 full 需在 root/chroot 下跑。
5. **变更摘要**：`gzh diff-ebuild <old_ebuild> <new_ebuild>`，总结改动与理由。
6. **提交**：`gzh commit <new_ebuild> <Manifest> [-m "category/package: add <PV>"]`（默认不 drop）。commit 无 AI 署名。
7. **交付**：默认停在此（本地分支 + commit）。开 PR 需显式：`gh pr create --repo Gentoo-zh/gentoo-zh --base master --head $(gh api user --jq .login):<branch>`（head 动态取，不写死）。

**重试上限 3 次**：任一硬门/编译同一错误重复 2 次即停，报告失败步骤+错误+每次尝试，询问继续/跳过/放弃。
