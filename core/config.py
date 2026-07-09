"""统一配置：路径、常量、环境变量。其他模块只从这里拿配置。"""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # 读取项目根目录的 .env

ROOT = Path(__file__).parent.parent
KB_DIR = ROOT / "knowledge_base"
DB_DIR = str(ROOT / "chroma_db")

COLLECTION = "senior_agent"
TOP_K = 5           # 最终喂给 LLM 的片段数
CANDIDATES = 20     # 混合检索召回的候选数（重排前）
MAX_CHUNK_CHARS = 800

# HF 镜像（下载 embedding / reranker 模型用），在 import transformers 前生效
if os.environ.get("HF_ENDPOINT"):
    os.environ.setdefault("HF_ENDPOINT", os.environ["HF_ENDPOINT"])


def llm_model() -> str:
    return os.environ.get("LLM_MODEL", "deepseek-chat")


def rerank_model() -> str:
    """重排模型名；设为 off/none/空 则禁用重排。"""
    return os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-base")
