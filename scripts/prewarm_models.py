"""提前下载并实际调用 embedding/reranker，避免首次线上请求冷启动。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import rerank_model
from core.embeddings import get_embedder
from core.retrieval import load_reranker


def main() -> None:
    vectors = get_embedder()(["浙江大学校园政策检索预热"])
    if not vectors or not vectors[0]:
        raise RuntimeError("embedding 预热未返回向量")
    print(f"embedding ok: dim={len(vectors[0])}")

    configured = rerank_model().strip().lower()
    if configured in {"", "off", "none", "0"}:
        print("reranker disabled")
        return
    reranker = load_reranker()
    if reranker is None:
        raise RuntimeError("reranker 已配置但加载失败")
    scores = reranker.predict([("校园卡丢失", "校园卡丢失后应立即挂失并补办。")])
    if len(scores) != 1:
        raise RuntimeError("reranker 预热返回异常")
    print(f"reranker ok: model={rerank_model()}")


if __name__ == "__main__":
    main()
