# 结论：退回

审查基线是 `gzh-skills@68e5790` 和 `gzh-agents@d5cad58`。本轮只执行只读检查，并与已合并的 `ab956762` 对拍。

## 上轮 12 项复核

| 项 | 状态 | 证据 |
| --- | --- | --- |
| 1 | 部分关闭 | 扫描、提交、rebase 的顺序已修正；但 bump 第 49 行和 new-package 第 47 行未暂存完整改动。 |
| 2 | 关闭 | bump 第 10、32–35 行改用已注册的完整 overlay。`repository.eselect:243–250` 的空 local 方案仍不成立，但已不再使用。 |
| 3 | 关闭 | 三份 skill 均改为 `qlist -v`。 |
| 4 | 部分关闭 | review 第 6 行直接覆盖八份规则；但第 11–18 行只看 diffstat，bump 第 6 行仍漏 desktop 和 eclass 路由。 |
| 5 | 未关闭 | bump 第 57–64 行未先展示标题和正文，且无条件开 PR；new-package 第 49–58 行没有交付正文。 |
| 6 | 未关闭 | bump 第 28 行的确认晚于第 19 行；内核规则第 10 行要求此前先下载增量补丁。其余显式下载已有门禁。 |
| 7 | 关闭 | review 第 32–35、52 行改为引用 `Dependencies and Revisions`，不再缩窄合法证据。 |
| 8 | 未关闭 | 三份 skill 仍使用 `${TMPDIR}`；review 第 39 行还设置环境变量；new-package 第 26 行使用非 POSIX 的 `less`。 |
| 9 | 部分关闭 | 已删去或补齐多数占位符；bump 第 10 行的 `<checkout>` 未列在第 8 行。 |
| 10 | 部分关闭 | 错误路径和 preflight 顺序已修正；规则仍在多处复述，review 第 45 行仍没有命令块。 |
| 11 | 部分关闭 | 用户级 Claude/Codex 目标和两个 plugin 的 `./skills/` 均正确；README 第 19–24 行未创建父目录。 |
| 12 | 关闭 | bump 第 46–49 行和 new-package 第 44–47 行已有 GPG 可用性分支。 |

`gzh-agents` 的八份规则文件都存在。但 `desktop-integration.md:1–3`、`eclass-discovery.md:1–3`、`pr-text.md:1–3` 并没有 `Read with`，与本轮说明不符。

## 必改项

1. **P0 — 完整改动没有进入提交。** bump 第 24–26、46–49 行中，`git mv` 只暂存重命名时的旧内容；后续编辑不会自动暂存。new-package 第 47 行没有任何暂存步骤，会被 `pkgdev` 报为无已暂存改动。先执行可失败的 clean-tree 门禁，再显式暂存全部预期文件，或使用已核实存在的 `pkgdev commit --all`。
2. **P0 — bump 强制删除旧版本，漏掉联动包。** bump 第 24–26、49、61 行固定为 `git mv` 和 `add NEW, drop OLD`。改为按 `version-bumps.md` 与 `kernels.md` 决定新增、保留、删除及联动文件；PR 标题取最终提交主题。
3. **P0 — PR 确认边界仍不存在。** bump 第 57–64 行在同一命令块中生成正文并执行 `gh pr create`，没有先显示正文或等待确认；还会虚构不存在的 issue。new-package 第 49–58 行也未显示正文。拆成准备、确认、发布三段；非 routine 或没有 `gh` 时只交付标题、正文、文件和 compare 链接。
4. **P0 — 安装失败会被 `tee` 隐藏，内核没有通用 smoke command。** bump 第 34–40 行、new-package 第 38–41 行和 review 第 21 行取得的是 `tee` 的退出状态。改用保留 `emerge` 状态的命令。只在规则要求且包提供可执行入口时做 smoke test；不要要求单机用户用命令启动新内核。
5. **P1 — diff、路由和发布命令仍不完整。** review 第 11–18 行须读取完整 diff 和明确文件清单；bump 第 6 行须补 desktop、eclass 路由。bump 第 55 行和 new-package 第 53 行须按 overlay `AGENTS.md:65` 在 rebase 后使用 `--force-with-lease`。
6. **P1 — 仓库合同与正文不符。** 用已列出的 `<log>` 等占位符替代 `${TMPDIR}`，移除 review 第 39 行的环境变量写法，并处理 `less` 与任意 `<smoke-command>`。规则条件只保留文件及节名引用；给 review 第 45 行一个命令块或并入相邻步骤。README 第 19 行先创建两个用户级父目录。

## 最强反例

输入是 bump `sys-kernel/gentoo-cjk-kernel`：`<old>=7.2.4`，`<new>=7.2.5`。期望结果是已合并提交 `ab956762`。

1. 第 1–2 步可在已注册且干净的完整 overlay 中执行；但第 2 步按 `kernels.md:10` 下载增量补丁，早于第 4 步确认。
2. 第 3 步删除 7.2.4。实际提交保留 7.2.4，新增 7.2.5 和 `virtual/dist-kernel-7.2.5-r100.ebuild`。
3. 第 5 步能看到 overlay 的 `cjktty.eclass`，说明旧 local-repo 缺陷已修正；但管道会隐藏 `emerge` 失败。
4. 第 6 步无法填写可返回流程的 `<smoke-command>`：唯一主机必须重启才能启动新内核。
5. 第 8 步只会提交已暂存的重命名，遗漏编辑后的 ebuild 和新增 virtual；第 9 步至多扫描出残缺提交，不能得到 `ab956762`。
6. 该改动不是 routine bump。第 10 步仍执行 `gh pr create`，标题还错误地写成 `add 7.2.5, drop 7.2.4`。

## 七条判据

| 判据 | 结论 | 依据 |
| --- | --- | --- |
| 逻辑 | 否 | bump 第 24–26、46–64 行 |
| 好维护 | 否 | 本仓库 `AGENTS.md:3`；三份 skill 仍复述规则 |
| 可读 | 否 | bump 第 10 行；PR 分支只写在叙述中 |
| 通用 | 否 | bump 第 24–26、36–40 行不能覆盖内核 |
| 易用 | 否 | new-package 第 47 行没有可提交的暂存内容 |
| 兼容 | 否 | README 第 13 行与实际环境变量、命令不符 |
| 不过度设计 | 否 | 上述 P0 阻断项仍未解决 |
