---
name: connect-zju-source
description: >
  探索一个浙大/学院网站并把来源接入知识库自动采集与更新流水线。按
  scripts/sources.yaml + scripts/kb_crawl.py 的机制，把一个新学院网站或新栏目
  从零接入：Playwright 探索结构 → 识别高价值栏目 → 登记来源 → 抓取验证 →
  审核收尾。MUST trigger when the user asks to: "接入学院网站/新来源/新栏目"、
  "探索一下 XX 网站"、"添加来源"、"接入自动采集"、"connect a source"、
  "kb crawl 新站点"，或任何想把外部网站纳入知识库自动更新流程的请求。
  也适用于给已有来源加新栏目。
---

# 接入新学院来源（Connect ZJU Source）

> **跨 agent 流程文档**：Claude Code / Codex / OpenCode / OpenClaw 等 agent 都按本文件执行；
> 仓库根 `AGENTS.md` 指向这里。
> 本 skill 与它的审核要点会**根据实际运行状态持续增补**（见第五步）。

把一个新学院/部处网站接入 `scripts/kb_crawl.py` 的自动采集流程，产出登记在
`scripts/sources.yaml` 的来源配置 + 一批通过校验的暂存文档。

**产出**：`sources.yaml` 新增一条 enabled 来源 + `knowledge_base/staging/<name>/` 下
通过 schema 校验的文档 + 一份给负责人的审核要点。

**前置**：`scripts/kb_crawl.py`、`scripts/sources.yaml` 已存在（本 skill 不重建它们）。
爬虫依赖在 **`requirements-crawl.txt`**（playwright + pypdf，叠加于主 requirements；不进应用镜像）。
在项目的 Python 环境里运行（venv 激活按平台：Windows `mentor/Scripts/activate`、macOS/Linux `mentor/bin/activate`）。
装完后若浏览器未装，需先跑一次 `python -m playwright install chromium`（**注意**：该命令装完整版 chromium + 独立 headless shell 两套；持久化上下文 `launch_persistent_context` 的 headless 模式用的是 headless shell，缺了会报 `Executable doesn't exist ...chromium_headless_shell`）。

## 为什么按这个顺序

固定爬虫（kb_crawl.py）只负责"按配置抓取 + 机械性元数据"，判断和价值筛选是人的
（或 agent 的一次性探索）活。所以接入流程 = **一次性把站点的结构和栏目价值摸清，
固化成 sources.yaml 配置，之后稳态每天/每周跑爬虫即可**。抓取容易自动化，判断才
是瓶颈——本 skill 把判断步骤显式化。

## 第一步：探索站点结构（Playwright）

用 `references/explore-site.py`（把 `TARGET_URL` 换成目标站）跑一遍，或手动确认以下 6 件事：

1. **导航结构**：首页/各子站有哪些 `list.htm` / `list.psp` 栏目（`<a href>` 里筛）。
2. **高价值栏目**：见第二步的价值排序。
3. **正文容器选择器**：WebPlus 系统统一是 `.wp_articlecontent`（libweb、office.ckc 都是）；
   其他 CMS 要自己找正文容器（在详情页 inspect DOM，找正文 text 所在的 div）。
4. **详情 URL 模式**：WebPlus 是 `^http(s)?://<域>/\d{4}/\d{4}/c<栏目号>a<文章号>/page\.htm$`。
   两种情况二选一：① 详情 URL 栏目号 ≠ 列表页路径（office.ckc 的 `gztz` 列表对应 `c54289`）→
   pattern 用**栏目号**收窄，避免抓到侧栏/别的栏目；② 列表页**聚合多栏目文章**
   （cs.zju.edu.cn 的招生页混着 c27006/c27010/c27011）→ pattern 用宽匹配 `c\d+a\d+`，
   因为 `discover_detail_urls` 只扫指定 `list_url` 那一页，宽 pattern 不会多抓别的页。
5. **PDF 陷阱**：WebPlus 政策正文常是 `iframe.wp_pdf_player` 嵌 PDF（`.wp_articlecontent`
   里只有 iframe）。爬虫已能自动抽（pypdf）或写附件占位，但探索时**要知道哪些栏目是
   PDF 型的**——它们价值高但正文在 PDF 里，别因为"正文空"误判成没内容。
6. **分页**：有 `list2.htm` 说明列表会分页。爬虫目前只抓第一页，探索时记下
   `list2.htm` 是否存在（分页支持是已知限制）。

## 第二步：判断高价值栏目

| 类型 | 价值 | 举例 |
|---|---|---|
| 规章制度/行政文件/实施细则/管理办法/办事指南/官方 Q&A | **高**（长期有效，能回答问题） | 图书馆规章制度、竺院行政文件、培养方案 |
| 通知/公告（有截止日期、有适用范围） | 中（时效性强，需人工判断 valid） | 评奖通知、选拔通知、报名通知 |
| 新闻/活动/动态/公示/获奖名单 | **低**（一次性，进库污染） | 本馆新闻、活动预告、名单公示 |

优先登记高价值栏目；中价值栏目可登记但要接受大量"待审/失效"；低价值栏目不登记。

## 第三步：登记来源到 sources.yaml

在 `scripts/sources.yaml` 的 `sources:` 下加一条。关键字段：

| 字段 | 规则 |
|---|---|
| `name` | kebab-case，唯一，用作暂存区目录名 |
| `source_org` | 实际发布单位（KB_FORMAT source_org） |
| `authority_level` | `university`（浙江大学）/ `department`（部处/图书馆）/ `college`（学院） |
| `category` / `source_type` | 政策/official_policy · 通知/official_notice · FAQ/official_guide，按第二步价值定 |
| `target_dir` | 最终知识库子目录，如 `政策/竺可桢学院`（暂存按此摆放，便于与现有文档 diff） |
| `list_url` + `detail_url_pattern` | 列表页 URL + 详情 URL 正则（**用详情里的栏目号**，见第一步第 4 条） |
| `content_selector` | 默认 `.wp_articlecontent`；非 WebPlus 站点手动确认 |
| `publish_rule` | `title_revision`（标题带"（YYYY年M月D日修订）"）否则 `url_date` |
| `normalize_headings` | 政策文档建议 `true`（把"一、二、三"短行提为 `##` 利切块） |
| `defaults` | `tags`（检索词）、`applies_to`/`campuses`/`colleges`——不确定一律 `未明确` |
| `maintainer` | 负责人标识；未定 `unassigned` |
| `frequency` | daily / weekly |

**元数据纪律**（爬虫已内置，登记时别破坏）：
- `title` 用页面标题，去掉「（YYYY年M月D日修订/更新）」后缀 → doc_id 跨版本稳定。
- `publish_date`：标题修订日 > 详情 URL 日期 > `unknown`；**绝不拿抓取日期冒充**。
- 无法确定的字段写 `未明确` / `unknown` / `needs_review`，不脑补。

**登记完跑一遍覆盖检查**：`python scripts/kb_crawl.py --check-coverage --site <base_url>`
枚举站点导航全部栏目与注册比对，把漏注册栏目显性化（先要注册成具体 `c<栏目号>`；
`c\d+a\d+` 通用规则的站点会整站视为已覆盖，检查意义有限）。

## 第四步：抓取验证

先 dry-run 看提取质量，再完整抓取：

```bash
python scripts/kb_crawl.py --source <name> --dry-run --limit 3
python scripts/kb_crawl.py --source <name>
```

**检查清单（抓完必查）**：
- manifest：`knowledge_base/staging/<name>/_manifest.json` 的 `errors` 应为空。
- 暂存文档：随机抽查 2-3 篇正文，确认无乱码、表格保留、附件链接带完整域名。
- schema：`python scripts/govern_kb.py`（查 real KB 不受影响）或逐个 `validate_metadata`。
- **空壳检查**：正文 <100 字的要警惕——若来源是 PDF 型栏目且没抽出正文，是抽取失败需排查；若本来就短（如名单通知）则正常。

## 第五步：审核要点与收尾

暂存区文档一律 `needs_review`，交给负责人前列出审核要点。**运行
`python scripts/kb_review_checklist.py` 自动生成 `knowledge_base/staging/review_checklist.md`
**（含人工维护的审核要点 + 通用启发式，按来源分表；清单随 staging 一起进 private repo），
把这份清单连同新增/更新的来源名交给负责人。以下要点也是生成清单的素材：

- **近重复**：manifest 里 `status: 新增` 但标题像已有文档的（组织前缀/多余空格/拆分的 part1-part2），是"标题变体"，需归并到同一 doc，不是重复新增。
- **时效与 valid**：过期年份的简章/通知（如 2022 版招生）要判 `valid: false`；新版给旧版标 `supersedes`。
- **栏目混杂**：行政文件栏目常混"获奖名单公示"等时效性通知，审核时重分类或剔除。
- **PDF 正文**：pypdf 已能抽中文 PDF；若个别 PDF 是扫描件会回退附件占位，需人工处理。

### 审核清单使用方法

1. **生成**：`python scripts/kb_review_checklist.py` → 产出 `knowledge_base/staging/review_checklist.md`（每次抓取后重跑即可）。
2. **逐条过**：清单按来源分表，每篇一行的"审核要点"是给负责人的建议。负责人对每条做出决定：`valid: true/false`、是否归并近重复、是否重分类（通知/政策/剔除）、是否给旧版标 `supersedes`。
3. **把决定写回暂存文档**：编辑对应 `knowledge_base/staging/<source>/<...>.md` 的 frontmatter（改 `valid`、`supersedes`、`category` 等），不要只在清单里打勾——清单只是工作台，暂存文档才是发布源。
4. **发布**：决定做完后跑下方发布链路。发布后暂存区即可清空。
5. **标 verified**：已核验的文档把 `review_status` 改为 `verified`，并填 `last_checked_at` + `maintainer`（KB_FORMAT 要求）。

> **清单会持续增补**：本 skill、`scripts/kb_review_checklist.py` 顶部的审核要点 `NOTES`、
> `scripts/sources.yaml` 都**根据实际运行状态不断增补**——每次接入/审核遇到的新判断
> （新坑、新栏目价值评估、新的近重复模式、新的 supersedes 关系）都应回写进去，
> 让下一次接入和审核更快更准。跑过几轮之后，NOTES 是逐步变厚的资产。

**验收后**才走发布链路（本次接入只到暂存区，发布由负责人确认）：

```bash
python scripts/govern_kb.py
python ingest.py
python tests/eval_retrieval.py
```

## 常见坑速查（都踩过，别重踩）

0. **SSO 保护来源**：部分栏目详情页会跳转浙大统一身份认证。**先分清是「登录问题」还是「权限问题」**：
   - **权限问题**：登录成功但目标页提示"无权限访问"（如 sis 行政文件只对外国语学院成员开放）→ 换账号也无用，**自动采集不了**，在 sources.yaml 标 `enabled: false` + 注明原因，别浪费精力做登录。
   - **登录问题**（非权限）：一次性登录 `python scripts/kb_crawl.py --login <列表URL> --channel msedge`（有头 Edge 登录 → 回车），cookie 存 `data/kb_crawl_profile/`（gitignored），之后 headless 复用；或从正常浏览器用 Cookie-Editor 导出 JSON 后 `--import-cookies <文件>` 注入。
   - **有头 chromium 在某些 Windows 上起不来（`spawn UNKNOWN`，GUI 缺依赖）→ 用 `--channel msedge` 走系统 Edge**。
   - 本机环境差异（如 PowerShell `os.path.exists` 对同一路径返回 False 而 bash 返回 True）会让某个终端完全跑不了 Playwright——**爬虫命令统一在 Git Bash 里跑**。

1. **Windows 路径分隔符**：清理暂存文件时 `str(Path(x))` 出反斜杠、`as_posix()` 是正斜杠，两者比对永不相等会把刚写的文件当"过期"删掉。比对用 `Path(x).as_posix()`。
2. **整轮全失败不清暂存**：站点挂掉时本轮 0 成功，绝不能清理上次暂存（保留待审材料）。kb_crawl 已内置该守卫。
3. **别跨 Python/JS 传 DOM 对象**：`query_selector_all` 返回的 ElementHandle 不能当普通对象用；把转换逻辑整段放进浏览器内 `evaluate` 的 JS 里。
4. **PDF 型栏目**：WebPlus 政策正文是 pdfjs iframe 嵌 PDF，`.wp_articlecontent` 看着"空"。pypdf 能抽中文（实测 71% CJK、24 页完整）；抽不动才写附件占位。
5. **详情 URL 的栏目号 ≠ 列表路径**：`detail_url_pattern` 用详情 URL 里的 `c<栏目号>` 限定，防止抓到侧栏/其他栏目的文章。
6. **标题变体**：同一政策在页面标题/手工文档间可能有组织前缀或多余空格 → doc_id 不同。这是暂存审核要归并的"近重复"，不是 bug，别在爬虫里硬凑。
7. **元数据保守**：`publish_date` 只认"标题修订日 → URL 日期 → unknown"；`applies_to/campuses/colleges` 不确定写 `未明确`。绝不拿抓取当天冒充发布日期。
8. **Windows 非法文件名字符**：标题里可能出现 `| : * ? " < >` 等（如 physics 的 "Nature | xxx"），写文件名前必须替换，否则写入直接 OSError。`build_filename` 已内置 `_safe_filename_part`。
9. **同源站点容器不一致**：同一站点不同栏目正文容器可能不同（如 sis 行政文件页是 PDF 附件页，无 `.wp_articlecontent`）。探索时别只看一个样本页就定 `content_selector`。
10. **覆盖检查只对「具体 c 号」敏感**：`detail_url_pattern` 写成 `c\d+a\d+` 通用规则的站点（cs/math/css 等）会被整站视为已覆盖，`--check-coverage` 不会暴露漏栏目。给新栏目登记时尽量用具体 `c<栏目号>`，覆盖检查才有意义。
11. **并行探测过多域名会误判"超时"**：一次性并发 urllib 探测几十个候选域名时，站点（尤其依赖 RVPN 的办公网）会被误判成"挂掉/超时"，其实是被并发请求打崩或响应变慢。**域名确认后必须单点重测**再下结论——生科办公网 `www.cls.office.zju.edu.cn` 就被并行探测误判过，用户单点确认实际可达。批量探域只用于"筛候选"，定性前逐个复核。

