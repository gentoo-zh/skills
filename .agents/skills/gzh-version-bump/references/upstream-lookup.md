# 上游版本查询策略

`gzh upstream-version <cat/pkg>` 内部策略（见 spec §6）：

1. **NvcheckerProvider（默认）**：读 overlay 的 `.github/workflows/overlay.toml`，提取该包 source 配置，调 `nvchecker` 取最新版。source 类型：`github`（use_latest_release/use_latest_tag）、`git`、`pypi`、`apt`、`regex` 等（见 nvchecker 文档）。
2. **PyPIProvider（回退）**：overlay.toml 无该包条目时，查 `https://pypi.org/pypi/<pn>/json`，取 `info.version`。注意返回的是 PEP 440 版本，需按 A2 规范化（`1.2rc1`→`_rc1`、`.postN`→`_pN`、`.devN`、epoch `1!`）；且 `info.version` 只给最新稳定版——源码包命名前务必核对上游真实 tag，别直接拿 PyPI 串当版本。
3. 无结果：返回 `source=none` + advisory。

返回结构：`{\"cat_pkg\", \"upstream\", \"source\", \"advisory\"}`。`advisory` 非空时，gzh-version-bump 的 A8 步骤应补 overlay.toml 条目（`gzh nvchecker-config set`）。

**新包无配置**：上游类型判断后用 `gzh nvchecker-config set <cat/pkg> --json '{\"source\":\"github\",\"github\":\"org/repo\",\"use_latest_release\":true}'`。`set` 用 tomlkit 改写 overlay.toml：**独立注释块（含被改条目上方的注释）都会保留**；只有**被修改条目自身的行内注释和内部格式会丢**（该条目先 `del` 再按纯 dict 重加）。此外 `sort_overlay_toml` 会按 cat/pkg 字母序**重排所有 block**、把 block 间空行规整为一行，新插入的 block 可能让相邻注释与包错位。文档场景是给**无 overlay.toml 条目的新包**补配置，此时 `del` 是空操作、本无自身注释可丢，实际改动只是重排/空行/注释错位——所以**务必人工 review diff**。

## overlay.toml 条目规范（加 nvchecker 追踪前逐条过）

写进 `.github/workflows/overlay.toml`（被 nvchecker.yml → bumpbot 消费、开 `[nvchecker] cat/pkg can be bump to X` issue）前，每条都过一遍：

1. **别刷屏，但「发布频繁」本身不是硬拦。** 真正的问题是「更新频繁 *且* 每次 bump 都要人工 review」——那会淹没 issue tracker。查节奏 `gh api repos/OWNER/REPO/releases?per_page=8 -q '.[].published_at'`。频繁但能无人值守自动 bump 的（低风险 `-bin` 重打包）加进来无妨；频繁又要人工盯的（如 v2ray-geoip / v2ray-domain-list-community 数据文件）保持注释、别激活。
2. **一个上游 repo 只追一个 variant。** `-bin` 和它的源码同胞指向同一个 github repo 会搞乱 github graphql API。加之前先 grep master 的 overlay.toml 找同胞；同胞已在追就别加你的（例：chezmoi 只追一个、chezmoi-bin 留 FIXME）。
3. **尊重已注释的条目。** `#[...]` 是有原因的（刷屏频率、需要 prefix 过滤、或同胞 graphql 冲突）。别盲目取消注释。规矩：要么删注释并加、要么保留注释并不加——绝不「既留注释又加包」。
4. **release vs tag + prefix。** 逐个核 tag：`gh api repos/OWNER/REPO/releases/latest -q .tag_name`。`use_latest_release` 走 GitHub release、`use_latest_tag` 走 git tag，选错会追到不同的版本流。若 tag 是 `v1.2.3`，必须 `prefix = \"v\"`，否则 nvchecker 永远误报（newver `v1.2.3` ≠ overlay `1.2.3`）。
5. **本地先验。** push 前 `nvchecker --file overlay.toml --keyfile <keyfile>`（keyfile 内容 `[keys]\\ngithub=\"<token>\"`）跑一遍确认能取到版本；至少也要 `python -c 'import tomllib,pathlib; tomllib.loads(pathlib.Path(\"overlay.toml\").read_text())'` 确认 toml 合法。`github_account` 是 nvchecker 忽略的自定义键——tomllib 合法只是必要条件，nvchecker load 成功才是真检查。

条目模板（github release 源）：
```
[\"cat/pkg\"]
source = \"github\"
github = \"OWNER/REPO\"
use_latest_release = true
prefix = \"v\"          # 仅当上游 tag 带 v 前缀
github_account = \"<维护者 login>\"
```

## github_account（bumpbot issue CC）

bumpbot 会把条目里的 `github_account`（字符串或数组）CC 到 issue 正文。解析要精确：

- **CC 具体维护者 login，不 CC 团队。** 绝不用 `github_account = \"gentoo-zh/overlay\"`——那会 ping 整个团队。没有合适的人就整行省掉（留空）。
- **解析 login 的步骤：**
  1. 读 `cat/pkg/metadata.xml`。`<maintainer type=\"person\">` 就是维护者；`type=\"project\"`（gentoo-zh）或没有 metadata → 用事实上的打包人 = 最早提交（添加）该包的人。
  2. 权威解析成 GitHub **login**：拿到 ta 添加该包的 commit sha（`git log --diff-filter=A --format=%H -- cat/pkg | tail -1`），再 `gh api repos/gentoo-zh/overlay/commits/<sha> -q .author.login`。
  3. **别拿 metadata 的 `<name>` 当 login**——那是显示名（例：显示名 \"Amar Begovic\" 的 login 是 `AmarBego`）。一律用 commit author 解析，并 `gh api users/<login> -q .login` 确认存在。
- 无人物（no metadata / type=project）时，**最早的添加者优于「最近」或「最频繁」提交者**——最近/最频繁常是跨包 QA-sweep 的顺手改动或批量 bump，不是真维护者。2+ 共同维护者用数组。
