# Gentoo-zh skills

gentoo-zh overlay 维护的 opencode/claude skill 套件 + `gzh` Python 工具。

## 安装
```bash
pip install -e ./gzh          # 安装 gzh CLI
# symlink skill 到发现路径（opencode/claude 均兼容 .agents/skills/）：
ln -s "$PWD/.agents/skills/gzh-version-bump" ~/.agents/skills/gzh-version-bump
ln -s "$PWD/.agents/skills/gzh-bump-from-issues" ~/.agents/skills/gzh-bump-from-issues
export GZH_OVERLAY_DIR=/path/to/gentoo-zh-dev-checkout
```

## 文档
- 设计: `docs/superpowers/specs/2026-07-04-gentoo-zh-maintenance-skill-design.md`
- 实现: `docs/superpowers/plans/2026-07-04-gentoo-zh-maintenance-mvp.md`
- devmanual 索引: `docs/devmanual.md`
- 约定: `AGENTS.md`

> 注：docs/superpowers/{specs,plans}/ 为 2026-07-04 历史设计快照，早于后续维护教训，且仍写旧仓库名 Gentoo-zh/gentoo-zh。**规范仓库是 gentoo-zh/overlay**。运行时以 AGENTS.md + 各 SKILL.md + docs/devmanual.md + overlay CI workflow 为准，spec 仅背景参考。
