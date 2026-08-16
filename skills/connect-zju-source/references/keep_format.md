# keep.txt 格式规范（审计发布清单）

keep.txt 是子代理审计暂存区文章后产出的"保留清单"，`scripts/promote.py` 依据它把文章发布到 knowledge_base。本文件定义 keep.txt 的命名与行格式规范。

## 命名规范

文件存放于 `data/kb_staging/audit/`，命名规则：

- **标准**：`<site>_agentX_keep.txt`，X 为大写字母 A-Z，多 agent 并行审计时每批一个文件
  - 例：`cmic_agentA_keep.txt`、`cmic_agentB_keep.txt`、`cmic_agentC_keep.txt`
- **单批次站点**：可省略 `_agentX`，即 `<site>_keep.txt`
  - 例：`soc_keep.txt`
- **同 agent 多轮**：数字后缀 `_agentB1` / `_agentB2`
  - 例：`marx_agentB1_keep.txt`（同一 agent 拆两批）
- **中间件/最终件**（可选约定）：`_merged_keep.txt` 表示合并中间件，`_final_keep.txt` 表示最终清单
  - 例：`cst_agent_final_keep.txt`；promote 的 glob 会优先 `_final` 并排除 `_merged`

**禁止无前缀命名**（如 `agentA_keep.txt`）。无前缀文件在多站点并行时会互相覆盖——例如 `agentA_keep.txt` 曾被 cbeis 与 cps 同时使用，内容互相污染，promote 时会把别站文章发进知识库。必须始终带 `<site>_` 前缀。

## 行格式

每行一条，UTF-8 编码，忽略空行与前后空白。

**普通行**（原样发布，不改分类）：
```
<staging相对路径>
```
例：
```
cmic-bktz/通知/传媒学院/本科通知/关于浙江大学传媒学院2026年秋季学期选课安排的通知_浙江大学传媒与国际文化学院_2026.md
```

**重分类行**（`|` 分隔，src 为 staging 相对路径，dst 为目标 KB 路径）：
```
<staging相对路径>|<KB相对路径>
```

dst 可写完整文件路径，也可只写目录（promote 自动补源文件名）：
```
cmic-bktz/通知/传媒学院/本科通知/传媒学院本科生转专业工作细则_浙江大学传媒与国际文化学院_2026.md|政策/传媒学院/本科培养
```

## 约束

- **路径前缀**：每行 src 的首段必须是 `<site>-<来源>`（如 `cmic-bktz`）。promote 会校验并跳过不匹配的行（防御共享文件污染）。
- **目标分类**：dst（或 src 原样分类）首段必须 ∈ `{政策, 通知, FAQ}`，且须与运行 `promote.py --categories` 一致。
- **md 后缀**：每行必须以 `.md` 结尾（目录缩写除外，promote 自动补）。

## 生命周期

1. 审计子代理产出 keep 清单到 `data/kb_staging/audit/<site>_agentX_keep.txt`
2. 运行 `python scripts/promote.py <site> [--categories 2|3]`（`--dry-run` 先核对）
3. 发布完成后 keep 文件即可清理；`data/kb_staging/_promote_<site>_final.json` 是发布凭证（含每篇 name/src/size/fix）

## 示例模板

```text
<site>-<源>/通知/<学院>/<栏目>/<标题>_<机构>_<年份>.md
<site>-<源>/通知/<学院>/<栏目>/<标题>_<机构>_<年份>.md|政策/<学院>/<目标栏目>/<标题>_<机构>_<年份>.md
<site>-<源>/通知/<学院>/<栏目>/<标题>_<机构>_<年份>.md|政策/<学院>/<目标栏目>
```
