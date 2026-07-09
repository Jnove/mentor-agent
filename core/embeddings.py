"""
Embedding 后端，通过 .env 切换：
    EMBED_BACKEND=local   本地 sentence-transformers（默认）
    EMBED_BACKEND=api     OpenAI 兼容接口（LM Studio / Ollama / 云端均可）

注意：换了 embedding 模型必须重跑 python ingest.py --rebuild 重建向量库，
入库和查询必须用同一个模型，否则检索结果全是错的。
"""
import os

from core import config  # noqa: F401  确保 .env 已加载

BATCH = 64


def get_embedder():
    """返回 embed(texts: list[str]) -> list[list[float]]"""
    backend = os.environ.get("EMBED_BACKEND", "local")

    if backend == "api":
        from openai import OpenAI

        model = os.environ.get("EMBED_MODEL", "text-embedding-bge-m3")
        client = OpenAI(
            api_key=os.environ.get("EMBED_API_KEY")
            or os.environ.get("LLM_API_KEY", "lm-studio"),
            base_url=os.environ.get("EMBED_BASE_URL")
            or os.environ.get("LLM_BASE_URL"),
        )

        def embed_api(texts):
            out = []
            for i in range(0, len(texts), BATCH):
                res = client.embeddings.create(model=model, input=texts[i:i + BATCH])
                out.extend(d.embedding for d in res.data)
            return out

        return embed_api

    # 默认：本地 sentence-transformers
    from sentence_transformers import SentenceTransformer

    st_model = SentenceTransformer(os.environ.get("EMBED_MODEL", "BAAI/bge-small-zh-v1.5"))

    def embed_local(texts):
        return st_model.encode(
            texts, normalize_embeddings=True, show_progress_bar=len(texts) > 20
        ).tolist()

    return embed_local
