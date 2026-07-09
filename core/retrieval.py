"""
混合检索：向量（chroma）+ BM25（jieba 分词）两路召回，
RRF 融合出候选，再用交叉编码 reranker 精排。

reranker 加载失败（模型没下载/断网）时自动降级为只用 RRF 结果，不影响可用性。
"""
import os
import re
import sys
from collections import defaultdict

from core.config import CANDIDATES, TOP_K, rerank_model

_TOKEN_CLEAN = re.compile(r"[^\w一-鿿]+")


def tokenize(text: str) -> list[str]:
    """jieba 搜索模式分词，去掉标点和空白。"""
    import jieba

    return [t for t in jieba.lcut_for_search(text) if _TOKEN_CLEAN.sub("", t)]


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion：合并多路召回的 id 排名，返回融合后的 id 列表。"""
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, id_ in enumerate(ranking):
            scores[id_] += 1.0 / (k + rank + 1)
    return sorted(scores, key=lambda i: scores[i], reverse=True)


def load_reranker():
    """加载交叉编码重排模型；失败返回 None（检索自动降级）。"""
    model_name = rerank_model()
    if model_name.strip().lower() in ("", "off", "none", "0"):
        return None
    try:
        # 本机代理（如 Clash）常导致 HF 下载失败，模型下载走镜像直连
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            os.environ.pop(key, None)
        from sentence_transformers import CrossEncoder

        return CrossEncoder(model_name, max_length=512)
    except Exception as e:
        print(f"[retrieval] reranker 加载失败，降级为 RRF 排序: {e}", file=sys.stderr)
        return None


class Retriever:
    """从 chroma collection 构建；BM25 索引建在内存里（几千块以内足够快）。

    注意：ingest 之后需要重建 Retriever（重启 app）才能让 BM25 看到新文档。
    """

    def __init__(self, embed, col, reranker=None):
        self.embed = embed
        self.col = col
        self.reranker = reranker

        data = col.get(include=["documents", "metadatas"])
        self.ids = data["ids"]
        self.docs = {i: d for i, d in zip(data["ids"], data["documents"])}
        self.metas = {i: m for i, m in zip(data["ids"], data["metadatas"])}

        self.bm25 = None
        if self.ids:
            from rank_bm25 import BM25Okapi

            self.bm25 = BM25Okapi([tokenize(self.docs[i]) for i in self.ids])

    def _vector_channel(self, query: str, n: int) -> list[str]:
        res = self.col.query(query_embeddings=self.embed([query]), n_results=n)
        return res["ids"][0]

    def _bm25_channel(self, query: str, n: int) -> list[str]:
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self.ids, scores), key=lambda x: x[1], reverse=True)
        return [i for i, s in ranked[:n] if s > 0]

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        if not self.ids:
            return []
        n = min(CANDIDATES, len(self.ids))
        fused = rrf_fuse([
            self._vector_channel(query, n),
            self._bm25_channel(query, n),
        ])[:n]
        hits = [{"text": self.docs[i], **self.metas[i]} for i in fused]

        if self.reranker is not None and len(hits) > 1:
            scores = self.reranker.predict([(query, h["text"]) for h in hits])
            hits = [h for _, h in sorted(
                zip(scores, hits), key=lambda x: x[0], reverse=True
            )]
        return hits[:top_k]
