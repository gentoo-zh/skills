# `gzh nvcheck-audit` — nvcheck 配置一致性检查

- **日期**: 2026-07-04
- **状态**: Draft（设计已逐节确认，待转 plan）
- **仓库**: `Gentoo-zh/skills`
- **范围**: 阶段 2 补充 —— 新 `gzh nvcheck-audit` 命令，检查 overlay.toml（nvchecker 配置）与实际 ebuild 包的一致性，对漏配包启发式推断上游并可选自动补配置。
- **前置**: MVP（`gzh ebuild-parse`/`nvchecker-config`/`repo`）、`gzh drop-old`（阶段 2 剩余）已交付。

---

## 1. 背景与目标

gentoo-zh 的 nvchecker CI 依赖 `.github/workflows/overlay.toml`（248 个包的 source 配置）检测上游新版本、开 bump reminder issue。**现状不一致**：实际 ebuild 包 475 个，其中 **227 个未配 nvcheck**（含 `acct-group/*`/`virtual/*` 等无上游的系统包噪音），**0 个残留**（配置有但包已删——干净）。

漏配意味着这些包的上游新版本不会被 nvchecker 检测、不会开 bump issue，长期滞后。`gzh nvcheck-audit` 自动化发现：
- **漏配 missing**：实际包 ∉ overlay.toml（过滤系统包噪音后）→ 启发式推断上游，可选 `--apply` 补配置。
- **残留 stale**：overlay.toml 配置 ∉ 实际包（包已删但配置残留，持续监控）。

无现成工具（pkgdev/gentoolkit 无此检查），gentoo-zh 自身无审计脚本。

---

## 2. 设计原则

| 原则 | 落实 |
|---|---|
| 启发式推断（确定 + 可解释）| 从 ebuild HOMEPAGE/SRC_URI 正则推断上游类型；不调网络/LLM |
| dry-run 安全 | 默认只列；`--apply` 才写 overlay.toml |
| 复用不造轮子 | `gzh ebuild-parse`（读 HOMEPAGE/SRC_URI）、`gzh nvchecker-config set`（写配置）、`gzh repo` |
| 系统包过滤 | 默认排除 `acct-*`/`virtual/*` 等无上游包；`--no-filter-system` 含 |
| overlay.toml 重写警告 | `nvchecker-config set` 重写丢注释，输出明确提示人工 review diff |

---

## 3. 命令接口

```
gzh nvcheck-audit [--apply] [--no-filter-system]
```

| 选项 | 默认 | 用途 |
|---|---|---|
| `--apply` | 关（dry-run）| 对漏配且推断≠unknown 的包调 `nvchecker-config set` 补配置 |
| `--no-filter-system` | 关（过滤）| 含 `acct-*`/`virtual/*` 等系统包到检查范围 |

退出码：0（成功，含空结果）；1（overlay.toml 解析失败等）。

---

## 4. 检查逻辑

读 overlay.toml（`tomllib`）取配置包集合 `configured`；遍历 `root/*/*/` 取实际包集合 `actual`（含 `*.ebuild` 的目录）。

- **stale（残留）** = `configured - actual`（配置有、包无）。
- **missing（漏配）** = `actual - configured`，再过滤系统包（默认）：
  - 系统包判定：category ∈ `{acct-group, acct-user, virtual}` 或 PN 以特定前缀（系统账户/虚拟，无上游）。
  - `--no-filter-system` 时不过滤。

---

## 5. 上游推断（启发式，纯函数）

对每个 missing 包，`gzh ebuild-parse <最高 ebuild>` 取 HOMEPAGE/SRC_URI，按优先级正则匹配：

| 模式（HOMEPAGE 或 SRC_URI 含）| 推断 source | 生成的 overlay.toml entry |
|---|---|---|
| `github.com/<org>/<repo>` | `github` | `{"source":"github","github":"<org/repo>","use_latest_release":true}` |
| `pypi.org` / `files.pythonhosted.org` / ebuild `inherit pypi` | `pypi` | `{"source":"pypi","pypi":"<pn>"}` |
| `gitlab.com` / `codeberg.org` / URL 以 `.git` 结尾 | `git` | `{"source":"git","src":"<url>","use_max_tag":true}` |
| 都不匹配 | `unknown` | 不生成（跳过 --apply，列警告）|

- github 提取：正则 `github\.com/([^/]+)/([^/)\."]+)`，取 `org/repo`（去 `.git`/`/`）。
- 优先级：github > pypi > git（同时匹配取更具体的）。
- 推断是纯函数（输入 ebuild 字段 + pn，输出 source/entry），可单测。

---

## 6. --apply（补配置）

对 missing 且推断≠unknown 的包：
- 调 `gzh nvchecker-config set <cat/pkg> --json '<entry>'`（复用现有命令，写 overlay.toml）。
- `nvchecker-config set` 重写 overlay.toml（**丢注释**，已知行为），输出明确提示「overlay.toml rewritten, comments lost; review the diff」。
- 每包一个 set 调用（或批量聚合后一次写——MVP 逐包调，简单）。
- 推断=unknown 的包：跳过 set，列 `skipped_unknown`。

---

## 7. 输出 JSON

```json
{
  "ok": true,
  "stale": ["cat/removed-pkg"],
  "missing": [
    {"cat_pkg": "app-misc/foo", "source": "github",
     "entry": {"source":"github","github":"org/foo","use_latest_release":true},
     "applied": false}
  ],
  "skipped_unknown": ["cat/bar"]
}
```
- `stale`：残留配置包列表。
- `missing`：漏配包（含推断 source + entry + applied 标志）。
- `applied`：dry-run 时全 false；`--apply` 时对非 unknown 包 true。
- `skipped_unknown`：推断为 unknown 的漏配包。

---

## 8. 测试策略

| 层 | 测什么 | 怎么测 |
|---|---|---|
| **上游推断 L1**（重点）| github/pypi/git/unknown 四类；github org/repo 提取；优先级；`.git`/`/` 清理；HOMEPAGE vs SRC_URI 都试 | pytest，纯函数（输入 dict 字段 + pn → source/entry），无网络 |
| **检查逻辑**| stale/missing 集合差集；系统包过滤（acct-group/virtual 被过滤）；`--no-filter-system` 含 | tmp_path 假 overlay（overlay.toml + cat/pkg 目录） |
| **--apply**| 对非 unknown 调 nvchecker-config set（mock runner）；unknown 跳过；applied 标志 | mock `set_entry`/runner，不真写 |
| **dry-run**| 不调 set；applied 全 false | 同上 |
| **集成**| 端到端 tmp overlay（overlay.toml + 几个包 + HOMEPAGE）→ 输出结构正确 | tmp_path |

复用：`gzh ebuild_parser.parse_ebuild`（读字段，可 mock 或真用）、`gzh.nvchecker_config.set_entry`（mock runner）。

---

## 9. 去个人化与安全边界

**去个人化：**
- overlay 根走 `find_overlay_root()`；无个人路径。
- 无 owner/maintainer 维度。

**安全边界：**
- **默认 dry-run**：不写 overlay.toml。
- `--apply` 写 overlay.toml（`nvchecker-config set` 重写丢注释），输出醒目提示 + 建议人工 review diff。
- 不碰 `/var/db/repos/gentoo-zh`（synced 副本）。
- 推断不准的风险：`--apply` 前应 dry-run review 推断结果（entry 字段）。

---

## 10. 交付边界与验收

**包含：**
- `gzh/gzh/nvcheck_audit.py`（`infer_source`/`audit`/`run_audit` 纯函数 + 检查/推断/apply 编排）
- `gzh/gzh/cli.py` 注册 `nvcheck-audit` 命令
- `gzh/tests/test_nvcheck_audit.py`（L1 全 mock + tmp_path fixture）
- 无新文档/skill（确定性命令，显式调）

**不包含：** 自动开 PR 补配置（停本地修改）、推断不准的人工修正 UI（列 unknown 供人工）、批量 nvchecker 验证（补配置后能否真查上游靠 CI nvchecker）、其他 source 类型（apt/regex 等，按需后加）。

**验收：**
1. `gzh nvcheck-audit`（dry-run）真实跑 → 输出 missing（过滤系统包后，应 < 227）+ stale（当前 0）+ skipped_unknown（HOMEPAGE 非 github/pypi/git 的）。
2. `--no-filter-system` → missing 含 acct-group 等。
3. 推断测试：github HOMEPAGE → github source + 正确 org/repo；pypi 包 → pypi；git URL → git；无匹配 → unknown。
4. `--apply`（tmp overlay）→ 对非 unknown 调 set_entry（mock）；unknown 跳过。
5. dry-run 不调 set_entry。
6. L1 pytest 全绿。

---

## 11. 后续（独立刀）

- **推断增强**：更多 source（apt/regex）、从 metadata.xml upstream 字段、从 ebuild 注释。
- **自动开 PR**：`--apply` + push + PR（停本地修改的进阶）。
- **CI 定时审计**：cron 跑 nvcheck-audit，漏配超阈值告警（notify telegram）。
- **与 drop-old 联动**：drop 包后 nvcheck-audit 清残留（当前 0，持续监控）。
