"""
混合检索：向量（chroma）+ BM25（jieba 分词）两路召回，
RRF 融合出候选，再用交叉编码 reranker 精排。

reranker 加载失败（模型没下载/断网）时自动降级为只用 RRF 结果，不影响可用性。
"""
import os
import re
import sys
from collections import defaultdict

from core.config import (
    CANDIDATES, COVER_MAX_EXTRA, COVER_MIN_SCORE, PROMPT_MIN_SCORE, TOP_K,
    rerank_model,
)
from core.slang import expand_query

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


def pick_with_coverage(ranked: list[tuple[float, dict]], top_k: int,
                       min_score: float = COVER_MIN_SCORE,
                       max_extra: int = COVER_MAX_EXTRA) -> list[dict]:
    """重排后的最终选择：先收敛到"每文档一块"凑满 top_k，空位再按分补块，
    最后给"得分够高但文档未覆盖"的候选补位。

    枚举类问题（"求是科学班有哪几种"）里所有相关文档得分都接近 1，
    top_k 往往被少数几篇的多个块占满，导致 LLM 数不全——
    第一步在得分达到覆盖阈值的文档中强制每文件最多占一块，让更多不同文档能进 top_k；
    第二步用空位把高分块补回，只有一篇相关文档时行为不变；
    第三步给 top_k 之外的高分未覆盖文档补位。
    细节类问题里无关文档得分接近 0，达不到 min_score，行为不变。
    """
    picked: list[dict] = []
    picked_objects: set[int] = set()
    covered: set[str] = set()
    # 先在高分候选中收敛到每文档一块，避免单文档多块占满（枚举类问题数不全的根因）
    for score, h in ranked:
        if len(picked) >= top_k:
            break
        f = h.get("file")
        if score >= min_score and f and f not in covered:
            picked.append(h)
            picked_objects.add(id(h))
            covered.add(f)
    # 空位按分补块：只有一篇相关文档时，该文档的高分多块仍能回到结果
    for _, h in ranked:
        if len(picked) >= top_k:
            break
        if id(h) not in picked_objects:
            picked.append(h)
            picked_objects.add(id(h))
    for score, h in ranked:
        if len(picked) >= top_k + max_extra:
            break
        if score >= min_score and h.get("file") not in covered:
            picked.append(h)
            picked_objects.add(id(h))
            covered.add(h.get("file"))
    return picked


def load_reranker():
    """加载交叉编码重排模型；失败返回 None（检索自动降级）。"""
    model_name = rerank_model()
    if model_name.strip().lower() in ("", "off", "none", "0"):
        return None
    try:
        # 部署环境可能只能通过显式代理下载模型；尊重调用方提供的代理变量。
        # 无代理时 HF_ENDPOINT 仍默认走镜像，已有本地模型路径也不受影响。
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
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

        self.catalog = self._build_catalog()

    def _build_catalog(self) -> list[dict]:
        """知识库全部文档的元数据清单（按文件路径排序，同文件夹相邻），
        注入 prompt 供枚举类问题数全，并参与统一的来源编号。"""
        seen: dict[str, dict] = {}
        for m in self.metas.values():
            m = m or {}
            f = str(m.get("file", ""))
            if f and f not in seen:
                seen[f] = m
        return [seen[f] for f in sorted(seen)]

    def _vector_channel(self, query: str, n: int) -> list[str]:
        res = self.col.query(query_embeddings=self.embed([query]), n_results=n)
        return res["ids"][0]

    def _bm25_channel(self, query: str, n: int) -> list[str]:
        if self.bm25 is None:
            return []
        scores = self.bm25.get_scores(tokenize(query))
        ranked = sorted(zip(self.ids, scores), key=lambda x: x[1], reverse=True)
        return [i for i, s in ranked[:n] if s > 0]

    def search(self, query: str, top_k: int = TOP_K,
               min_score: float = PROMPT_MIN_SCORE,
               carry_ids: tuple = ()) -> list[dict]:
        """min_score 是重排分入围下限；eval 调参时传 -1 可看到全部候选的分数。

        carry_ids：上一轮命中的块 id，追问时并入候选池一起重排——只保送进候选、
        不保送进结果，去留由重排分说话。避免改写后的检索式把上一轮的依据整体
        漂移掉、答案前后矛盾。无 reranker 时忽略（没有分数无从裁决）。
        """
        if not self.ids:
            return []
        query = expand_query(query)  # 黑话 -> 正式名词，两路召回和重排共用
        n = min(CANDIDATES, len(self.ids))
        fused = rrf_fuse([
            self._vector_channel(query, n),
            self._bm25_channel(query, n),
        ])[:n]
        if self.reranker is not None:
            fused += [i for i in carry_ids if i in self.docs and i not in fused]
        hits = [{"id": i, "text": self.docs[i], **self.metas[i]} for i in fused]

        if self.reranker is not None and len(hits) > 1:
            scores = self.reranker.predict([(query, h["text"]) for h in hits])
            for s, h in zip(scores, hits):
                h["score"] = float(s)  # 透出重排分，eval/调参用
            hits.sort(key=lambda h: h["score"], reverse=True)
            # 低分候选不入围：库外问题所有候选都接近 0，全被过滤时返回 []，
            # 上层据此给"无据"信号而不是硬凑 top_k 条噪音
            ranked = [(h["score"], h) for h in hits if h["score"] >= min_score]
            return pick_with_coverage(ranked, top_k)
        return hits[:top_k]
