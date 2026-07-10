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

# 覆盖补位：top_k 之外、重排得分仍 >= COVER_MIN_SCORE 的"未覆盖文档"各补最优一块。
# 枚举类问题（"有哪几种"）所有相关文档得分都高，top_k 装不下会自动补齐；
# 细节类问题无关文档得分接近 0，不触发补位，行为不变。
COVER_MIN_SCORE = 0.5
COVER_MAX_EXTRA = 5  # 最多补几块（总片段数 <= TOP_K + COVER_MAX_EXTRA）

# HF 镜像（下载 embedding / reranker 模型用），在 import transformers 前生效
if os.environ.get("HF_ENDPOINT"):
    os.environ.setdefault("HF_ENDPOINT", os.environ["HF_ENDPOINT"])


def llm_model() -> str:
    return os.environ.get("LLM_MODEL", "deepseek-chat")


def rerank_model() -> str:
    """重排模型名；设为 off/none/空 则禁用重排。"""
    return os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-base")


AUTH_DB = str(ROOT / "data" / "auth.db")


def allowed_email_domains() -> list[str]:
    """注册邮箱后缀白名单（小写、去 @），默认仅 zju.edu.cn。"""
    raw = os.environ.get("ALLOWED_EMAIL_DOMAINS", "zju.edu.cn")
    return [d.strip().lstrip("@").lower() for d in raw.split(",") if d.strip()]


def admin_emails() -> list[str]:
    """管理员邮箱白名单：名单里的邮箱注册即为管理员，已注册的下次登录自动提升。

    只提升不降级——从名单移除不会撤销管理员（否则会和管理页手动授予的管理员打架），
    撤销请走管理页。这里只放邮箱，不放密码：密码始终由本人注册时设定。
    """
    raw = os.environ.get("ADMIN_EMAILS", "")
    return [e.strip().lower() for e in raw.split(",") if e.strip()]


def auth_secret() -> str:
    """cookie 签名密钥，缺失直接报错（不默默用弱密钥）。"""
    s = os.environ.get("AUTH_SECRET", "")
    if not s:
        raise RuntimeError(
            "AUTH_SECRET 未配置：在 .env 里设置随机字符串，"
            "可用 python -c \"import secrets; print(secrets.token_hex(32))\" 生成"
        )
    return s


def session_days() -> int:
    return int(os.environ.get("SESSION_DAYS", "7"))
