# 真实问题与评测样本规范 v1.0

> 用于收集学生原始问题，并逐步整理为可重复运行的 RAG/回答评测集。

## 1. 两阶段数据

### Raw：原始问题

保留真实措辞，用来观察口语、黑话、上下文缺失和用户真实目标。收集人只做脱敏，不润色问题。

### Golden：标准评测样本

由另一名成员复核，补充期望行为、答案要点和有效来源。只有 `review_status: verified` 的样本进入正式回归集。

推荐使用 UTF-8 JSONL：一行一个 JSON 对象，便于追加、去重和批量运行。

## 2. 字段

### Raw 必填

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | 整数 | 当前为 `1` |
| `question_id` | 字符串 | 唯一 ID，如 `q-20260803-001` |
| `raw_question` | 字符串 | 脱敏后的原始表达，不改成书面语 |
| `context` | 列表 | 多轮问题的前序消息；首轮为空列表 |
| `collected_at` | 日期 | `YYYY-MM-DD` |
| `collection_method` | 枚举 | `interview/survey/feedback/group_with_consent/other` |
| `collector` | 字符串 | 组员编号，不记录被访学生身份 |
| `topic` | 字符串 | 学籍、奖助、校园生活、信息化服务等 |
| `student_profile` | 对象 | 仅保留回答所需的学生类型、年级、学院、校区；未知填 `unknown` |
| `privacy_review` | 对象 | 是否完成脱敏、是否含敏感信息 |

### Golden 新增

| 字段 | 类型 | 说明 |
|---|---|---|
| `intent` | 枚举 | `fact/procedure/eligibility/deadline/link/listing/other` |
| `expected_behavior` | 枚举 | `answer/clarify/refuse/link_only` |
| `required_clarifications` | 列表 | 回答前必须追问的条件 |
| `gold_answer_points` | 列表 | 标准答案关键点，不要求固定文案 |
| `gold_sources` | 列表 | 正确知识库 `doc_id`、标题、URL 和适用日期 |
| `expected_files` | 列表 | 检索评测使用的知识库相对路径 |
| `review_status` | 枚举 | `draft/verified/rejected` |
| `reviewer` | 字符串 | 必须与收集人不同 |
| `reviewed_at` | 日期/null | 完成复核的日期 |
| `notes` | 字符串 | 冲突、边界或评分说明 |

## 3. JSONL 示例

```json
{"schema_version":1,"question_id":"q-20260803-001","raw_question":"NSEP一般啥时候报啊","context":[],"collected_at":"2026-08-03","collection_method":"interview","collector":"member-3","topic":"资助与实践","student_profile":{"student_type":"本科生","grade":"unknown","college":"unknown","campus":"unknown"},"privacy_review":{"anonymized":true,"contains_sensitive_data":false},"intent":"deadline","expected_behavior":"answer","required_clarifications":[],"gold_answer_points":["通常在每年11月申报","提醒以当年通知为准"],"gold_sources":[{"doc_id":"kb-438d3545b661e66c","title":"关于NSEP、SQTP和SRTP的Q&A","url":"https://mp.weixin.qq.com/s/mVgGm083ifEIc3tZXGWhkA","as_of":"2026-08-03"}],"expected_files":["FAQ/本科生院/关于NSEP、SQTP和SRTP的Q&A.md"],"review_status":"verified","reviewer":"member-4","reviewed_at":"2026-08-03","notes":"示例；正式入集前仍需复核发布时间和当年通知"}
```

## 4. 收集要求

- 每人从至少 5～8 名同学处收集约 30 条，共 150 条原始问题。
- 允许约 20% 语义重合，以保留同一问题的不同表达。
- 不得记录姓名、学号、手机号、身份证号、精确成绩、处分详情、健康情况等身份信息。
- 群聊问题仅在获得授权或完成不可逆脱敏后使用。
- 原始问题不能为了方便检索而提前改写。
- 收集人负责初步脱敏；复核人负责标准行为、答案和来源。

## 5. 标注规则

- 缺少学院、年级、校区等关键条件：`expected_behavior: clarify`。
- 知识库没有可靠依据：`expected_behavior: refuse`，不能把相似资料当答案。
- 用户只想找入口：可标 `link_only`，无需生成长答案。
- `gold_answer_points` 只写来源支持的事实；经验建议单独注明。
- 来源必须指向有效版本；时间敏感问题填写 `as_of`。
- 校级与院级材料冲突时，在 `notes` 说明适用优先级。

## 6. 复核与进入回归集

交叉复核顺序：1号→2号→3号→4号→5号→1号。

进入正式评测集前必须满足：

- [ ] 已脱敏，且不含可识别个人的信息。
- [ ] 收集人与复核人不同。
- [ ] 期望行为明确。
- [ ] 标准答案要点均有来源支持。
- [ ] 来源版本和适用范围已核对。
- [ ] `review_status` 为 `verified`。

评测至少分别报告：检索 hit@k/MRR、可回答性、追问准确率、拒答准确率、答案要点覆盖、引用支持率和链接正确率。
