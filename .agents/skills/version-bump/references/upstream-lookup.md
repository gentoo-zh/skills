# 上游版本查询策略

`gzh upstream-version <cat/pkg>` 内部策略（见 spec §6）：

1. **NvcheckerProvider（默认）**：读 overlay 的 `.github/workflows/overlay.toml`，提取该包 source 配置，调 `nvchecker` 取最新版。source 类型：`github`（use_latest_release/use_latest_tag）、`git`、`pypi`、`apt`、`regex` 等（见 nvchecker 文档）。
2. **PyPIProvider（回退）**：overlay.toml 无该包条目时，查 `https://pypi.org/pypi/<pn>/json`。
3. 无结果：返回 `source=none` + advisory。

返回结构：`{"cat_pkg", "upstream", "source", "advisory"}`。`advisory` 非空时，version-bump 的 A8 步骤应补 overlay.toml 条目（`gzh nvchecker-config set`）。

**新包无配置**：上游类型判断后用 `gzh nvchecker-config set <cat/pkg> --json '{"source":"github","github":"org/repo","use_latest_release":true}'`，注意 set 会重写 overlay.toml（丢注释），务必人工 review diff。
