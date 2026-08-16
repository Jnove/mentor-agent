# 测试

全部为 `python tests/<file>.py` 直接运行（非 pytest 收集），`PYTHONIOENCODING=utf-8` 保证中文输出。

## 检索回归（慢，需要已构建向量库）

- `eval_retrieval.py` — 检索质量回归集。对整库跑 85 个 golden 问题，报 hit@8 / MRR / 负例最高重排分 / 阈值误杀。改动检索链路、换 embedding/reranker、改切块后必跑。`python tests/eval_retrieval.py`

## 核心单元测试（离线毫秒级）

- `test_core.py` — core 纯函数：RRF 融合、切块、覆盖补位、build_context、引用重编号、问题改写、笔记导出
- `test_retrieval.py` — Retriever 主链路：内存 chroma + hash embedding + 假 reranker，验证召回/重排/过滤/去重
- `test_bst.py` — 百事通（s.zju.edu.cn）解析器，离线 HTML 夹具驱动

## 应用与认证

- `test_auth.py` — core/auth 配置与认证逻辑
- `test_cookie_gate.py` — app.py cookie 登录状态机（AppTest 驱动，锁定时序契约）

## 知识库治理与部署

- `test_govern_kb.py` — govern_kb 发布报告与迁移
- `test_ingest.py` — ingest 入库门禁（bad schema fail-closed）
- `test_kb_schema.py` — 知识文档 schema v2 校验
- `test_kb_paths.py` — KB 正式发布目录边界
- `test_config_paths.py` — 版本化发布的外置配置/状态路径
- `test_deploy_tools.py` — 部署预检、备份、安全恢复工具
