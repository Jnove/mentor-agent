# 知识库文档规范 v2.0

> 数据、采集、审核和 RAG 共用的唯一接口。所有知识先满足本规范，再进入索引。

## 1. 核心原则

1. **不伪造事实**：日期、适用范围、有效期不确定时写 `unknown`、`null` 或 `未明确`。
2. **来源分层**：官方材料与学生资料必须区分，学生资料不能包装成官方政策。
3. **先审核后确认**：自动迁移/抓取的资料默认 `needs_review`；人工核验后才能标 `verified`。
4. **原文优先**：正文保留原意和标题层级，不由采集者改写政策结论。
5. **可追溯**：每篇文档有稳定 `doc_id`、原文链接、维护人和核验状态。

## 2. 存放与命名

```text
knowledge_base/
├── 政策/主题/标题_来源单位_年份.md
├── 通知/主题/YYYY-MM-DD_标题_来源单位.md
└── FAQ/主题/标题_来源单位_年份.md
```

- UTF-8 编码，扩展名 `.md`。
- 一个文件只包含一个独立主题；超长原文可按章节拆分。
- 拆分文件必须保留相同来源，并在标题中注明部分。
- 不使用“最终版”“新版”等不稳定命名，年份或日期必须明确。

## 3. 完整 frontmatter 示例

```yaml
---
schema_version: 2
doc_id: kb-26d3f53cd43fef12
title: 本科生转专业管理办法
source_url: https://zdbk.zju.edu.cn/notice/123.htm
source_org: 本科生院
source_type: official_policy
authority_level: department
publish_date: 2025-09-01
category: 政策
tags: [转专业, 学籍, 本科生]
valid: true
review_status: verified
last_checked_at: 2026-08-03
maintainer: data-team-1
applies_to: [本科生]
campuses: [未明确]
colleges: [未明确]
effective_from: 2025-09-01
effective_until: null
supersedes: []
superseded_by: []
---
```

## 4. 字段说明

| 字段 | 类型 | 规则 |
|---|---|---|
| `schema_version` | 整数 | 必须为 `2` |
| `doc_id` | 字符串 | 稳定唯一 ID；不要随文件移动而修改 |
| `title` | 字符串 | 原文正式标题；学生资料使用原资料标题 |
| `source_url` | URL | 必须直达原文，不使用网站首页或搜索结果页 |
| `source_org` | 字符串 | 实际发布/编写单位 |
| `source_type` | 枚举 | 见“来源分级” |
| `authority_level` | 枚举 | `university/department/college/student/external/unknown` |
| `publish_date` | 日期/字符串 | `YYYY-MM-DD`；无法确定时为 `unknown`，禁止拿抓取日期冒充发布日期 |
| `category` | 枚举 | `政策/通知/FAQ` |
| `tags` | 列表 | 至少 1 个；推荐 2～8 个稳定检索词 |
| `valid` | 布尔值 | 是否允许进入有效知识集合；失效、示例、拒绝资料为 `false` |
| `review_status` | 枚举 | `needs_review/verified/rejected` |
| `last_checked_at` | 日期/null | 最近人工核验原文和时效的日期；未核验为 `null` |
| `maintainer` | 字符串 | 维护人/小组标识；未分配为 `unassigned` |
| `applies_to` | 列表 | 如 `本科生/研究生/本科新生`；不确定为 `[未明确]` |
| `campuses` | 列表 | 适用校区；不确定为 `[未明确]` |
| `colleges` | 列表 | 适用学院；不确定为 `[未明确]`，不可仅凭发布单位推断全校适用 |
| `effective_from` | 日期/null | 生效日期；未知为 `null` |
| `effective_until` | 日期/null | 失效日期；仍有效或未知为 `null`，结合 `valid` 判断 |
| `supersedes` | 列表 | 本文替代的 `doc_id` |
| `superseded_by` | 列表 | 替代本文的 `doc_id` |

## 5. 来源分级

| `source_type` | 含义 | 默认处理 |
|---|---|---|
| `official_policy` | 学校正式规章、管理办法 | 高优先级，但仍需核验时效 |
| `official_notice` | 学校、部门、学院正式通知 | 关注截止日期和适用范围 |
| `official_guide` | 官方办事指南、官方 FAQ | 可回答流程性问题 |
| `student_guide` | 学生编写的新生指引、经验整理 | 必须显示“学生资料，仅供参考” |
| `third_party` | 校外机构或第三方资料 | 原则上不回答政策结论 |
| `unknown` | 来源性质不明 | 不得标记为 `verified` |

来源优先级：`official_policy` → `official_notice` → `official_guide` → `student_guide` → `third_party/unknown`。

## 6. 审核状态与生命周期

```text
采集/迁移 → needs_review → verified
                      ↘ rejected（valid: false）
verified → 过期/被替代 → valid: false + superseded_by
```

- `needs_review`：结构已通过，但来源、正文或适用范围尚未完成人工核验。
- `verified`：必须填写 `last_checked_at` 和非 `unassigned` 的 `maintainer`。
- `rejected`：必须同时设置 `valid: false`。
- 抓取失败不等于政策失效，不得自动把现有文档设为无效。

## 7. 正文清洗

1. 保留原文标题层级（`##`、`###`），供 RAG 按章节切块。
2. 表格转换为 Markdown 表格，不使用截图代替正文。
3. 删除导航、点击量、页眉页脚、推荐阅读等网页噪音。
4. 保留办理入口、附件名称、截止时间、负责单位和联系方式。
5. 附件不能解析时写明附件名和原文入口，不猜测内容。
6. 学生资料必须保留来源说明，不与官方原文拼成同一篇文档。
7. 不自行总结或改写正式条款；解读由回答层完成。

## 8. 入库与验收

```powershell
# 只审计，不写文件
python scripts/govern_kb.py

# 迁移到 v2；仅删除内容完全一致的副本
python scripts/govern_kb.py --apply --remove-duplicates

# 校验通过后增量入库
python ingest.py
```

验收清单：

- [ ] schema v2 字段齐全且类型正确。
- [ ] `source_url` 是可定位原文的完整链接。
- [ ] 官方/学生/第三方来源分类正确。
- [ ] `valid` 与 `review_status` 不冲突。
- [ ] `verified` 文档有维护人和核验日期。
- [ ] 适用范围未知时明确写“未明确”，没有自动脑补。
- [ ] 正文无乱码、网页噪音和完全重复副本。
- [ ] 变更经过复核，并重新运行检索回归。
