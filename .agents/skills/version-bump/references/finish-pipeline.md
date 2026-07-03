# 收尾流程（执行器，所有硬门必须过）

阶段 A 产出"改好的新 ebuild"后，依次：

1. **校验（🔴硬门）**：`gzh lint <new_ebuild>`——devmanual 规则 + ~arch。Error 即停。
2. **Manifest（🔴硬门）**：`gzh manifest <new_ebuild>`——fetch distfiles + checksums。fetch 失败即停（检查 SRC_URI）。
3. **QA（🔴硬门 Error）**：`gzh pkgcheck <pkg_dir> --min-severity error`——Error 必须清零；Warning 记录不阻断。
4. **编译验证（🟡软门）**：`gzh build-test <new_ebuild> --level quick`（源码包默认 quick；`-bin` 包用 `full`；巨大/GUI 包用 `none` 但**必须**在交付报告注明跳过原因）。失败→提示转 fix-build-failure。
5. **变更摘要**：`gzh diff-ebuild <old_ebuild> <new_ebuild>`，总结改动与理由。
6. **提交**：`gzh commit <new_ebuild> <Manifest> [-m "category/package: add <PV>"]`（默认不 drop）。commit 无 AI 署名。
7. **交付**：默认停在此（本地分支 + commit）。开 PR 需显式：`gh pr create --repo Gentoo-zh/gentoo-zh --base master --head $(gh api user --jq .login):<branch>`（head 动态取，不写死）。

**重试上限 3 次**：任一硬门/编译同一错误重复 2 次即停，报告失败步骤+错误+每次尝试，询问继续/跳过/放弃。
