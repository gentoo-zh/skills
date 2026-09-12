# gentoo-zh skills

三份 skill，给在 [gentoo-zh/overlay](https://github.com/gentoo-zh/overlay) 上干活的 agent 用：

| skill | 做什么 |
| --- | --- |
| `gzh-bump` | 把一个现有包升到新版本 |
| `gzh-new-package` | 新增一个包 |
| `gzh-review` | 审一个包改动能不能合 |

规则只有一个来源：overlay 里的 `AGENTS.md` 和 `.agents/rules/`。这里只写步骤、命令和清单，不复述规则；每份 skill 第一行列出该先读哪些文件。

命令只用官方工具：`pkgdev`、`pkgcheck`、`eix`、`qlist`、`scanelf`、`gh`。`pkgdev` 和 `pkgcheck` 缺一即停；没有 `gh` 就给 compare 链接让人开 PR。没有任何环境变量或机器专属步骤。

## 安装

在 overlay 检出里做符号链接，不提交：

```bash
git clone https://github.com/gentoo-zh/skills ~/.local/share/gentoo-zh-skills
cd /path/to/overlay
mkdir -p .claude && ln -s ~/.local/share/gentoo-zh-skills/.agents/skills .claude/skills   # Claude Code
mkdir -p .codex  && ln -s ~/.local/share/gentoo-zh-skills/.agents/skills .codex/skills    # Codex
mkdir -p .omp    && ln -s ~/.local/share/gentoo-zh-skills/.agents/skills .omp/skills      # oh-my-pi
```

Claude Code 也可以当 plugin 装：`/plugin marketplace add gentoo-zh/skills`，再 `/plugin install gentoo-overlay-skills`。
