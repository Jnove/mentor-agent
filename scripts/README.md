# 脚本

按用途分组。除注明外均用 `mentor/Scripts/python.exe`（Windows venv）运行；中文输出加 `PYTHONIOENCODING=utf-8`。

## 爬取

- `kb_crawl.py` — 浙大站点抓取主入口：`--source <name>` 按 sources.yaml 抓栏目文章到 `data/kb_staging/`。`--dry-run --limit N` 先验质量
- `bst_crawl.py` — 百事通（s.zju.edu.cn）离线快照采集 → KB 暂存区
- `import_zju_welcome.py` — 导入 zju-welcome 新生指引

## 发布 / 治理

- `promote.py` — 通用发布：把审计 keep.txt 的篇目从 staging 复制到 knowledge_base 并改 frontmatter。`python scripts/promote.py <site> [--categories 2|3]`
- `govern_kb.py` — schema 审计、迁移、精确去重（`--apply` 才写回）
- `kb_review_checklist.py` — 生成暂存区审核清单 `knowledge_base/staging/review_checklist.md`
- `verify_needs_review.py` — 批量核验 needs_review 文档：`--scope` 按目录、`--apply` 转 verified、`--bad` 标 valid:false、`--fill-verified` 补齐 last_checked_at/maintainer
- `dedup_kb_staging.py` — staging 查重
- `classify_bst_staging.py` — 百事通暂存：去重 + 质量审查 + 分类入库/淘汰

> 入库命令 `python ingest.py` 在仓库根目录（不在 scripts/）。

## 修复

- `fix_bst_norm_mojibake.py` — 修复百事通规范性文件 PDF 抽取乱码
- `refetch_norm_pdf_body.py` — 重新抽取规范性文件 PDF 正文

## 运维

- `smoke_test.py` — 部署后 HTTP 冒烟（对线上 health/root 端点）：`python scripts/smoke_test.py --base-url https://... [--require-https]`
- `deploy_preflight.py` — 部署前配置/数据预检（纯标准库）
- `ops_backup.py` / `ops_restore.py` — 数据备份与恢复
- `ops_health_check.py` — 健康检查
- `ops_cert_check.py` — 证书检查
- `ops_systemd_backup.py` — systemd 服务备份
- `prewarm_models.py` — 预下载并调用 embedding/reranker，避免线上冷启动
- `make_admin.py` — 把已注册用户提升为管理员
- `update_kb.ps1` — 知识库子模块完整更新流水线（拉取 + govern + ingest，Windows PowerShell）

## 统计

- `kb_stats.py` — 知识库规模统计（来源数 / 入口 md 数 / 块数）

## 辅助

- `ocr.py` — 图片 OCR 提取（下载暂存 markdown 里的图片并转文字）
- `sources.yaml` — 所有抓取源配置（共享文件，不提交 git）

## 约定

- `sources.yaml`、`kb_crawl.py` 是多人共享文件：改动**不提交** git，只提交自己的站点脚本/产物。
- promote 的 keep.txt 格式规范见 `skills/connect-zju-source/references/keep_format.md`。
