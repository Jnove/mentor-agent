"""Retriever 主链路测试：纯内存 chroma + hash embedding + 假 reranker，离线毫秒级。

用法: python tests/test_retrieval.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["EMBED_BACKEND"] = "hash"  # 确定性哈希向量，不下载模型

import chromadb

from core.embeddings import get_embedder
from core.retrieval import Retriever

DOCS = [
    ("政策/转专业.md::0",
     "标题：转专业办法\n\n转专业需要在春季学期报名，绩点排名要求前百分之五十。",
     {"title": "转专业办法", "file": "政策/转专业.md"}),
    ("政策/转专业.md::1",
     "标题：转专业办法\n\n转专业面试安排在第八周，由转入学院组织。",
     {"title": "转专业办法", "file": "政策/转专业.md"}),
    ("FAQ/军训.md::0",
     "标题：军训指南\n\n军训在八月底开始，持续两周，注意防晒补水。",
     {"title": "军训指南", "file": "FAQ/军训.md"}),
    ("FAQ/食堂.md::0",
     "标题：食堂介绍\n\n食堂早餐六点半开门，晚餐供应到八点。",
     {"title": "食堂介绍", "file": "FAQ/食堂.md"}),
]


class FakeReranker:
    """文本含关键词给高分，否则给底分——确定性地驱动过滤/排序分支。"""

    def __init__(self, keyword: str, hi: float = 0.9, lo: float = 0.01):
        self.keyword, self.hi, self.lo = keyword, hi, lo

    def predict(self, pairs):
        return [self.hi if self.keyword in text else self.lo for _, text in pairs]


_seq = iter(range(1000))


def make_retriever(reranker=None, docs=DOCS):
    # EphemeralClient 在进程内是共享实例，集合名唯一化保证测试相互隔离
    col = chromadb.EphemeralClient().create_collection(
        f"test-kb-{next(_seq)}", metadata={"hnsw:space": "cosine"})
    embed = get_embedder()
    if docs:
        col.add(
            ids=[d[0] for d in docs],
            documents=[d[1] for d in docs],
            embeddings=embed([d[1] for d in docs]),
            metadatas=[d[2] for d in docs],
        )
    return Retriever(embed, col, reranker=reranker)


def test_search_no_reranker():
    r = make_retriever()
    hits = r.search("转专业需要什么条件")
    assert hits and any(h["file"] == "政策/转专业.md" for h in hits), hits
    assert len(hits) <= 5


def test_search_empty_collection():
    r = make_retriever(docs=[])
    assert r.search("任何问题") == []


def test_reranker_scores_and_ids_exposed():
    r = make_retriever(FakeReranker("军训"))
    hits = r.search("军训什么时候开始")
    assert hits[0]["id"] == "FAQ/军训.md::0"
    assert hits[0]["score"] == 0.9


def test_low_score_filtered_out():
    # 只有军训块得 0.9，其余 0.01 低于 PROMPT_MIN_SCORE 全部出局
    r = make_retriever(FakeReranker("军训"))
    hits = r.search("军训什么时候开始")
    assert [h["file"] for h in hits] == ["FAQ/军训.md"], hits
    # 全军覆没（没有任何块含关键词）时返回空列表——"无据"信号
    r2 = make_retriever(FakeReranker("库里不存在的词"))
    assert r2.search("量子力学第五版答案") == []


def test_carry_ids_join_rerank_pool():
    # 上一轮命中（转专业块）在本轮检索式下未被召回，靠 carry 进入候选池，
    # 且 reranker 给它高分后能进入最终结果——去留由分数说话。
    # 语料只有 4 块而 CANDIDATES=20 时两路必然全量召回、carry 变成空转，
    # 收窄 CANDIDATES 让目标块真的落在候选池外，并显式断言这个前提
    import core.retrieval as retrieval_mod

    r = make_retriever(FakeReranker("转专业"))
    old = retrieval_mod.CANDIDATES
    retrieval_mod.CANDIDATES = 2
    try:
        base = r.search("食堂几点开门", top_k=99, min_score=-1)
        assert all(h["id"] != "政策/转专业.md::0" for h in base), \
            "前提不成立：目标块已被常规召回，测试空转"
        hits = r.search("食堂几点开门", carry_ids=("政策/转专业.md::0",))
        assert any(h["id"] == "政策/转专业.md::0" for h in hits), hits
        # carry 的 id 不存在时安静忽略
        assert r.search("食堂几点开门", carry_ids=("不存在::9",)) is not None
    finally:
        retrieval_mod.CANDIDATES = old


def test_catalog_dedup_sorted():
    r = make_retriever()
    files = [m["file"] for m in r.catalog]
    assert files == sorted(set(files)) and len(files) == 3


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
