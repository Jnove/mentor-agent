# 学长组 Agent — RAG 骨架 (MVP)

## 结构

```
mentor-agent/
├── KB_FORMAT.md          # 知识库文档格式规范（数据组必读）
├── knowledge_base/       # 数据组往这里放 markdown 文档
├── ingest.py             # CLI：增量入库（--rebuild 全量重建）
├── app.py                # Streamlit 界面（纯 UI 层）
├── core/                 # 业务逻辑
│   ├── config.py         #   路径/常量/env 统一入口
│   ├── chunking.py       #   文档切块
│   ├── embeddings.py     #   embedding 后端（local/api 可切换）
│   ├── retrieval.py      #   BM25+向量混合召回 → RRF 融合 → 交叉编码重排
│   ├── llm.py            #   回答生成 / 多轮问题改写 / 笔记要点压缩
│   └── notes.py          #   来源去重 / FAQ 导出
├── tests/test_core.py    # 纯函数测试：python tests/test_core.py
├── .env.example          # 配置模板，复制为 .env 后填写
└── requirements.txt
```

## 快速开始

#### 1.安装依赖
```bash
pip install -r requirements.txt
```
> 这里建议使用虚拟环境
#### 2. 配置：复制 .env.example 为 .env，填入 API Key（Windows 直接复制粘贴改名即可）
>    LLM_API_KEY / LLM_BASE_URL / LLM_MODEL，任何 OpenAI 兼容接口都行

#### 3. 数据组按 KB_FORMAT.md 往 knowledge_base/ 放文档

#### 4. 建库（增量：只处理新增/变更的文档；文档有更新就重跑）
```
python ingest.py
#    换了 embedding 模型后必须全量重建：
# python ingest.py --rebuild
```

#### 5. 启动
```
streamlit run app.py
```

首次运行会下载中文 embedding 模型 bge-small-zh（约 100MB）和重排模型
bge-reranker-base（约 1.1GB，可在 .env 里设 `RERANK_MODEL=off` 跳过）。
国内网络走 hf-mirror 镜像；本机开着代理（Clash 等）导致下载失败时，
代码会自动绕开代理直连镜像。

## 检索管线

问题 →（多轮追问先由 LLM 改写成独立问题）→ 向量召回 + BM25 关键词召回
→ RRF 融合 top20 → bge-reranker 重排 → top5 + 覆盖补位 → 连同知识库目录（统一编号）喂给 LLM 生成。
LLM 在正文中用 [n] 标注每句话的来源；流式结束后按首次出现顺序重编号为 [1][2][3]…
（`renumber_citations`），回答下方的来源清单随之生成；LLM 没标注时退回"检索命中去重"兜底。

- 覆盖补位：枚举类问题（"求是科学班有哪几种"）下所有相关文档重排得分都高，
  top5 会被少数几篇的多个块占满；对得分 ≥0.5 但还没进结果的文档各补最优一块
  （最多 +5，见 `core/config.py`）。细节类问题无关文档得分接近 0，不触发。
- 知识库目录：全部文档标题清单随每次提问注入 prompt，保证"有哪些/多少个"
  这类问题即使补位装不下也能数全。
- 重排模型加载失败会自动降级为 RRF 排序，不影响可用性
- `ingest.py` 跑完后需重启 app，BM25 内存索引才能看到新文档

## 路演前检查

1. 准备 5~8 个演示问题，全部提前跑通（含一个追问，展示多轮改写）
2. 每个回答都应带来源链接（没带说明检索没命中，补文档）
3. 问一个知识库里没有的问题，确认它会说"不知道"而不是编造
4. 改动检索相关代码后跑 `python tests/test_core.py`

## 一个月版本 TODO

- [ ] 爬虫定时抓取通知 -> 自动生成规范 markdown -> 增量入库（已支持增量）
- [ ] 评测脚本：问题集批量跑分，防改动回归
- [x] 混合检索（BM25 + 向量）+ 重排，提升政策条款类问题命中率
- [x] 多轮追问改写（检索前把"那时间呢？"改写成独立问题）
- [ ] FastAPI 拆分后端接口，支持微信/QQ 机器人接入
- [ ] 笔记/对话落盘（SQLite）+ 回答有用性反馈
- [ ] 活动通知订阅推送
