# gentoo-zh skills

三份 skill，给在 [gentoo-zh/overlay](https://github.com/gentoo-zh/overlay) 上干活的 agent 用：

| skill | 做什么 |
| --- | --- |
| `gzh-bump` | 把一个现有包升到新版本 |
| `gzh-new-package` | 新增一个包 |
| `gzh-review` | 审一个包改动能不能合 |

规则只有一个来源：overlay 里的 `AGENTS.md` 和 `.agents/rules/`。这里只写步骤、命令和清单，不复述规则；每份 skill 第一行列出该先读哪些文件。

命令只用 Gentoo 工具（`pkgdev`、`pkgcheck`、`emerge`、`eix`、`qlist`、`scanelf`、`portageq`）、git、`gh` 和 POSIX 基本命令。`pkgdev` 和 `pkgcheck` 缺一即停；没有 `gh` 就给 compare 链接让人开 PR。没有环境变量、测试机或机器专属路径。

## 安装

链接到各客户端的用户级 skill 目录，不进 overlay 仓库：

```bash
git clone https://github.com/gentoo-zh/skills ~/.local/share/gentoo-zh-skills
mkdir -p ~/.claude/skills ~/.codex/skills
for s in gzh-bump gzh-new-package gzh-review; do
  ln -s ~/.local/share/gentoo-zh-skills/.agents/skills/$s ~/.claude/skills/$s   # Claude Code
  ln -s ~/.local/share/gentoo-zh-skills/.agents/skills/$s ~/.codex/skills/$s    # Codex
done
```

Claude Code 也可以当 plugin 装：`/plugin marketplace add gentoo-zh/skills`，再 `/plugin install gentoo-overlay-skills`。
