# Gentoo-zh skills

gentoo-zh overlay 维护的 opencode/claude skill 套件 + `gzh` Python 工具。

## 安装
```bash
pip install -e ./gzh          # 安装 gzh CLI
# symlink skill 到发现路径（opencode/claude 均兼容 .agents/skills/）：
ln -s "$PWD/.agents/skills/version-bump" ~/.agents/skills/version-bump
export GZH_OVERLAY_DIR=/path/to/gentoo-zh-dev-checkout
```

## 文档
- 设计: `docs/superpowers/specs/2026-07-04-gentoo-zh-maintenance-skill-design.md`
- 实现: `docs/superpowers/plans/2026-07-04-gentoo-zh-maintenance-mvp.md`
- devmanual 索引: `docs/devmanual.md`
- 约定: `AGENTS.md`
