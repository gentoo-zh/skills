# `gzh drop-old` — 旧版本清理设计

- **日期**: 2026-07-04
- **状态**: Draft（待用户 review）
- **仓库**: `Gentoo-zh/skills`
- **范围**: 路线图阶段 2 剩余 —— 新 `gzh drop-old` 命令，按规则清理旧 ebuild 版本，补 `gzh-version-bump`「只 add 不 drop」的缺口。
- **前置**: 阶段 0 MVP（`gzh` 11 子命令 + `gzh-version-bump` skill）、阶段 2 第一刀（`gzh bump-issues`）、阶段 3 雏形（`gzh-bump-from-issues` + triage + notify）已交付。`gzh outdated` 经评估**放弃**（bump-issues 已覆盖过期包发现）。

---

## 1. 背景与目标

`gzh-version-bump` 按设计「默认只 add 不 drop」（spec §12 A7）——每次 bump 新增 ebuild，旧版本累积。gentoo-zh 当前多版本包分布（排除 9999 liveup）：**350 包 1 版**（主流）、52 包 2 版、16 包 3 版、8 包 4+。需一个工具定期清理旧版本。

**无现成工具**（已联网核实）：
- Gentoo 官方 `pkgdev` 无 drop 命令（仅 commit/manifest/mask/push/showkw）。
- `gentoolkit` 的 `eclean` 清的是 distfiles/binpkgs（用户端缓存），**非 ebuild 旧版本**。
- gentoo-zh 自身无 drop 脚本/workflow（.github 仅有 nvchecker/pkgcheck/emerge-on-pr）。
- Gentoo dev 维护旧版本是手动 `git rm` 实践，无标准工具。

`gzh drop-old` 把这一手动实践自动化：按「保留最新 N 个非 liveup」规则列/删旧 ebuild + 重算 Manifest。

---

## 2. 设计原则

| 原则 | 落实 |
|---|---|
| 安全第一（破坏性操作）| **默认 dry-run**（只列将删，不真删）；`--apply` 才执行 |
| 版本排序正确 | 用 portage `vercmp`（非字典序），避免 `1.10` < `1.9` 之类错误 |
| 复用不造轮子 | `run_manifest`（MVP 已有）；无新依赖（portage 系统自带） |
| 不自动 commit | drop + Manifest 后停，review 由用户手动 `gzh commit` |
| liveup 保护 | `*-9999` 一律保留，不计入 keep、不被 drop |

---

## 3. 命令接口

```
gzh drop-old (--all | --pkg <cat/pkg>) [--keep N=2] [--apply]
```

| 选项 | 默认 | 用途 |
|---|---|---|
| `--all` | （必选其一）| 扫描 overlay 所有包 |
| `--pkg <cat/pkg>` | （必选其一）| 单包 |
| `--keep N` | `2` | 每包保留最新 N 个非 liveup 版本 |
| `--apply` | 关（dry-run）| 真删 ebuild + 重算 Manifest；缺省 = dry-run |

- `--all` 与 `--pkg` 互斥（都未传或都传 → click 报错）。
- 退出码：0（成功，含 dry-run 空候选）；1（manifest 重算失败等）。

---

## 4. drop 逻辑（纯函数，可单测）

```python
def list_ebuilds(pkg_dir: Path, pn: str) -> list[Path]:
    """返回 pkg_dir 下 <pn>-*.ebuild 列表（未排序）。"""

def drop_candidates(ebuilds: list[Path], pn: str, keep: int = 2,
                    vercmp=portage.versions.vercmp) -> tuple[list, list]:
    """返回 (drop, keep_list)。
    - 排除 *-9999（liveup，始终保留，不计入 keep）。
    - 其余按 vercmp 降序排（最新在前），保留前 keep 个，余为 drop。
    - vercmp 排序用 portage.versions.vercmp（系统库）。
    """
```

**版本提取**：从文件名 `<pn>-<PV>.ebuild` 取 PV（复用 `ebuild_parser._pv_from_name` 逻辑）。`-r1` 等修订号由 vercmp 正确处理。

**9999 判定**：`PV == "9999"` 或 `PV.startswith("9999")`（覆盖 `9999`/`99999999`）视为 liveup，保留。

---

## 5. 执行模式

**dry-run（默认）**：
- 输出 JSON 数组，每项：
  ```json
  {"cat_pkg": "app-misc/foo", "dropped": ["foo-1.0.ebuild", "foo-1.1.ebuild"],
   "kept": ["foo-1.2.ebuild", "foo-1.3.ebuild"]}
  ```
- 只列，不删、不改 Manifest、不 commit。

**--apply**：
- 对每个 `dropped` ebuild：`Path.unlink()`（删文件）。
- 每包删完调 `run_manifest`（pkgdev manifest 重算，移除已删 ebuild 的 distfile 条目）。
- manifest 失败 → 该包记 error，继续下一包（汇总报告）。
- **不自动 commit**。输出同 dry-run 结构 + 每包 `manifest_ok` 标志。

---

## 6. 测试策略

| 层 | 测什么 | 怎么测 |
|---|---|---|
| **纯函数 L1**（重点）| `drop_candidates`：vercmp 排序、keep 边界、9999 排除、修订号 `-r1`、多段版本 `1.10` vs `1.9` | pytest + `tmp_path` 建假 ebuild 文件名；mock `vercmp` 或用真实 portage |
| **list_ebuilds** | glob 正确、只取 `<pn>-*.ebuild` | tmp_path fixture |
| **--apply 模式** | 删 ebuild + 调 run_manifest（mock runner）；manifest 失败记 error 继续下一包 | tmp_path 多版本目录 + mock `run_manifest` runner |
| **dry-run 不删** | dry-run 后文件仍在 | tmp_path 验证 |
| **互斥参数** | `--all` 与 `--pkg` 都传/都不传 → click 非 0 | CliRunner |

**vercmp 依赖**：portage 是系统库（Gentoo 自带），测试直接用真实 `portage.versions.vercmp`（不需 mock），单测在 Gentoo 环境跑。

---

## 7. 去个人化与安全边界

**去个人化：**
- overlay 根走 `find_overlay_root()`（git toplevel 或 `$GZH_OVERLAY_DIR`），无个人路径硬编码。
- 无 owner/maintainer 维度（drop 是按版本规则，非按人）。

**安全边界：**
- **默认 dry-run**：无 `--apply` 不删任何文件。
- **不自动 commit**：drop + Manifest 后停，用户 review diff 再手动 `gzh commit`（避免批量 drop 后发现误删已 commit）。
- manifest 重算失败不中断（记 error，保留该包状态供诊断）。
- 不碰 `/var/db/repos/gentoo-zh`（synced 副本）；只在开发副本。
- `*-9999` liveup 一律保留（防删 liveup）。

---

## 8. 交付边界与验收

**包含：**
- `gzh/gzh/drop_old.py`（`list_ebuilds`/`drop_candidates`/`run_drop_old` 纯函数 + vercmp 排序）
- `gzh/gzh/cli.py` 注册 `drop-old` 命令
- `gzh/tests/test_drop_old.py`（L1 全 mock，含 vercmp 真实排序）
- 无新文档/skill（drop-old 是确定性命令，由 agent/用户显式调）

**不包含：** CI 定时自动 drop（偏阶段 3 自动值守）、stable/profile 依赖包的特殊保护（MVP 按通用 keep 规则）、distfiles 清理（eclean 已覆盖用户端）。

**验收：**
1. `gzh drop-old --pkg app-misc/cc-switch-cli --keep 2`（dry-run）→ 列出将 drop 的旧 ebuild（cc-switch-cli 有 5.4.0/5.5.0/5.8.7 三版 → drop 5.4.0、保留 5.5.0/5.8.7），不真删。
2. `--apply` → 真删 + Manifest 重算（mock 或真实 pkgdev）；文件确认删除。
3. `--all` dry-run → 扫描所有包，输出多包结果（含 dropped/kept）。
4. `*-9999` 在 keep 之外保留（如某包有 1.0/2.0/9999，keep 2 → drop 无，9999 额外保留）。
5. vercmp 正确：`1.10` > `1.9`（非字典序）。
6. L1 pytest 全绿；dry-run 不删文件。

---

## 9. 后续（独立刀）

- **CI 定时 drop**（阶段 3）：cron 跑 `gzh drop-old --all --apply` + commit + PR。
- **stable/profile 保护**：当 keep 规则成熟，识别被 profile 依赖/stable 的版本额外保留。
- **与 bump-from-issues 联动**：bump 成功后可选触发 drop-old（保持每包 N 版）。
- **distfiles 同步清**：drop ebuild 后，可选清对应 distfiles（现 eclean 在用户端，开发端可加）。
