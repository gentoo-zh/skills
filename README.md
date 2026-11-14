# gentoo-zh skills

一个 `gentoo-zh` 入口 skill，供在 [gentoo-zh/overlay](https://github.com/gentoo-zh/overlay) 上维护包的 agent 使用，按任务加载版本更新、新包或 PR 审查步骤，以及适用的检查项。

旧名称 `gzh-bump`、`gzh-new-package`、`gzh-review` 已由 `gentoo-zh` 替代。

这些 skill 不依赖专用 CLI。独立项目 [gentoo-zh/gzh](https://github.com/gentoo-zh/gzh) 已暂停，不得使用其 CLI。

规则只有一个来源：overlay 里的 `AGENTS.md` 和 `.agents/rules/`。这里只写步骤、命令和清单，不复述规则；入口列出先读的规则，步骤和共用清单放在同一 skill 目录的 `references/` 中。

命令只用 Gentoo 工具（`pkgdev`、`pkgcheck`、`emerge`、`eix`、`qlist`、`scanelf`、`portageq`）、git、`gh` 和 POSIX 基本命令。`pkgdev` 和 `pkgcheck` 缺一即停；没有 `gh` 就给 compare 链接让人开 PR。没有环境变量、测试机或机器专属路径。

## 安装

```bash
npx skills add gentoo-zh/skills -g
```

更新时执行 `npx skills update`。
