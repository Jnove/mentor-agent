# 知识库治理报告（2026-08-03）

## 结果

- 已将 137 份旧文档迁移到 `schema_version: 2`。
- 已删除 3 份逐字节完全一致的重复副本，保留“政策/违纪处分”目录下的原件。
- 当前共有 134 份文档；二次审计结果为 `changed=0`、`errors=0`、重复组为 0。
- 其中 133 份为 `needs_review`，1 份虚构示例为 `rejected + valid: false`；本次自动迁移没有把任何资料冒充为人工已核验。

## 来源分布

| 来源类型 | 数量 |
|---|---:|
| 学生指引 `student_guide` | 94 |
| 官方政策 `official_policy` | 21 |
| 官方指南 `official_guide` | 16 |
| 官方通知 `official_notice` | 2 |
| 未明确 `unknown` | 1 |

## 人工复核队列

### P0：补原文直链（6 份）

- `FAQ/校园生活/图书馆空间预约指南_图书馆_2025.md`
- `政策/招生政策/本科招生工作管理办法_浙江大学_2021.md`
- `政策/招生政策/竺可桢学院/图灵班/图灵班增补实施细则_计算机学院_2024.md`
- `政策/转专业/物理学院转专业工作细则_物理学院_2025.md`
- `通知/招生通知/竺可桢学院/竺可桢学院混合班招生_竺可桢学院_2025.md`
- `通知/转专业通知/数学科学学院转专业通知_数学科学学院_2025.md`

### P0：核实发布日期（1 份）

- `FAQ/本科生院/关于NSEP、SQTP和SRTP的Q&A.md`：旧字段误填成标题，已保守迁移为 `unknown`。

### P1：人工确认

- 为 133 份 `needs_review` 文档分配 `maintainer`，核验来源和适用范围后填写 `last_checked_at` 并改为 `verified`。
- 94 份学生指引只能作为参考来源；应用层需显式标注，且政策类结论优先采用官方材料。
- 对 `applies_to/campuses/colleges/effective_*` 中的未知值逐步补全，不得靠文件名猜测。

## 复现命令

```bash
# 只审计，不写文件
python scripts/govern_kb.py

# 执行迁移并删除仅限“内容完全一致”的副本
python scripts/govern_kb.py --apply --remove-duplicates --report governance.json

# 每次治理后执行；应看到 changed=0 和 errors=0
python scripts/govern_kb.py
```

原始知识文件按项目约定不提交 Git，因此以后可用上述脚本重复治理本地或服务器知识库。
