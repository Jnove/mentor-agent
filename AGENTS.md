# AGENTS.md — 给AI 助手的项目导览

本文件是一份跨平台约定，Claude Code / Codex / OpenCode / OpenClaw 等 agent 都能读取，
面向"第一次进入本仓库"的 AI，帮助快速定位和避免踩坑。仓库架构使用的详细规范见 `README.md`、
`DEPLOY.md`、`KB_FORMAT.md`。

## 这个项目是什么

浙大学长组 Agent：Streamlit 问答应用 + RAG（chroma 向量 + BM25 + reranker），
知识库 `knowledge_base/`（按 `KB_FORMAT.md` schema v2 治理，真实文档不进 git）。

## 知识库自动采集（AI 助手的重点）

本仓库自带一条"探索新学院/部处网站 → 接入自动采集流水线"的完整流程，见
**`skills/connect-zju-source/SKILL.md`**（任何 agent 都应按它执行）。核心组件：

- `scripts/sources.yaml` — 来源注册表（每个学院/栏目一条固定爬虫配置）
- `scripts/kb_crawl.py` — 采集器：按配置抓取 → 写暂存区 + manifest
- `scripts/kb_review_checklist.py` — 生成审核清单（内容不进 git）

流水线：**探索 → 登记来源 → 抓取 → 审核 → 发布**。审核由负责人确认后才发布。

## 常用命令

以下命令需在项目 Python 环境里运行（venv 激活方式因平台而异：Windows Git Bash 用
`mentor/Scripts/activate`，macOS/Linux 用 `mentor/bin/activate`，也可用 `.venv`/conda/系统 python）。

```bash
python scripts/kb_crawl.py --source <name>
```

发布链路（审核通过后，负责人执行）：
```bash
python scripts/govern_kb.py
python ingest.py
python tests/eval_retrieval.py
```
