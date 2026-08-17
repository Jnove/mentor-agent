"""统一配置：路径、常量、环境变量。其他模块只从这里拿配置。"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _configured_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default.resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


# 版本化发布时 .env 和持久化数据位于 release 目录外；本地开发仍使用仓库默认路径。
ENV_FILE = _configured_path("MENTOR_ENV_FILE", ROOT / ".env")
load_dotenv(ENV_FILE)

KB_DIR = _configured_path("MENTOR_KB_DIR", ROOT / "knowledge_base")
DB_DIR = str(_configured_path("MENTOR_CHROMA_DIR", ROOT / "chroma_db"))

COLLECTION = "senior_agent"
TOP_K = 8           # 最终喂给 LLM 的片段数
CANDIDATES = 20     # 混合检索召回的候选数（重排前）
# 块长要给 512 token 窗口留余量：bge-small-zh 嵌入和 bge-reranker 打分都在 512 处
# 静默截断（中文约 1 字 = 1 token），而入库时每块还要拼「标题/分类/检索标签」前缀
# （全库实测平均约 160 字），reranker 的窗口里 query 也占一份。改这个值后必须 --rebuild。
MAX_CHUNK_CHARS = 350

# 覆盖补位：top_k 之外、重排得分仍 >= COVER_MIN_SCORE 的"未覆盖文档"各补最优一块。
# 枚举类问题（"有哪几种"）所有相关文档得分都高，top_k 装不下会自动补齐；
# 细节类问题无关文档得分接近 0，不触发补位，行为不变。
COVER_MIN_SCORE = 0.5
COVER_MAX_EXTRA = 3  # 最多补几块（总片段数 <= TOP_K + COVER_MAX_EXTRA）

# 入围下限：重排得分（sigmoid 后 0~1）低于此值的候选不进 prompt。库外问题所有
# 候选都接近 0，全被过滤时检索返回空，由 build_context 给 LLM 显式"无据"信号，
# 不再喂 top_k 条噪音。阈值以 tests/eval_retrieval.py 的负例最高分校准，宁低勿高
# （漏放一条噪音的代价远小于误杀一条相关片段）。仅 reranker 可用时生效。
PROMPT_MIN_SCORE = 0.15

# 浙大百事通（s.zju.edu.cn）实时兜底检索：RAG 无命中或最高重排分低于阈值时，
# 调 core.bst 检索常见问题 + 校园资讯补进 prompt（离线快照 bst_crawl 之外的第二路）。
# .env 设 BST_FALLBACK=off 可关闭（如校内网络不可达 s.zju.edu.cn 时）。
BST_FALLBACK = os.environ.get("BST_FALLBACK", "on").strip().lower() not in ("off", "none", "0", "false")
BST_FALLBACK_SCORE = 0.3  # RAG 最高重排分低于此值才触发兜底（低于阈值说明候选不相关）
BST_TOP_N = 4             # 兜底最多取几条（FAQ 优先，不足补资讯）

# 服务器启动后在后台预热检索资源（embedding/reranker/BM25），用户首问不冷启动。
# 设 off 关闭（如服务器内存紧张或想避免启动期 CPU 占用）。
PREWARM = os.environ.get("PREWARM", "on").strip().lower() not in ("off", "none", "0", "false")

# HF 镜像默认值（下载 embedding / reranker 模型用），在 import transformers 前生效；
# .env / 环境变量里显式配置的值优先
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")


def llm_model() -> str:
    return os.environ.get("LLM_MODEL", "deepseek-chat")


def llm_model_light() -> str:
    """辅助任务（问题改写、笔记压缩）用的轻量模型；不配则回落主模型。

    这两个调用都阻塞在用户可感知的等待里（改写在检索前、压缩在答后 spinner），
    换 mini 档主要省延迟，其次省费用。"""
    return os.environ.get("LLM_MODEL_LIGHT") or llm_model()


def rerank_model() -> str:
    """重排模型名；设为 off/none/空 则禁用重排。"""
    return os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-base")


AUTH_DB = str(_configured_path("MENTOR_AUTH_DB", ROOT / "data" / "auth.db"))
USAGE_DB = str(_configured_path("MENTOR_USAGE_DB", ROOT / "data" / "usage.db"))
TEACHER_DB = str(_configured_path("MENTOR_TEACHER_DB", ROOT / "data" / "teacher.db"))
# 统一黑话表：RAG 检索黑话（type=rag，值=正式语词）与课程黑话（type=course，值=课程名列表）
# 由 knowledge_base 子仓库跟踪，主仓库只更新 submodule 指针
SLANG_FILE = _configured_path("MENTOR_SLANG_FILE", ROOT / "knowledge_base" / "slang.json")

# 查老师：聊天里识别到「评价某老师」提问时跳过 RAG，渲染结构化教师卡片。
# 设 off 关闭整条链路（回到纯 RAG）；TEACHER_SUMMARY 单独控制卡片里的 LLM 速评。
TEACHER_LOOKUP = os.environ.get("TEACHER_LOOKUP", "on").strip().lower() not in ("off", "none", "0", "false")
TEACHER_SUMMARY = os.environ.get("TEACHER_SUMMARY", "on").strip().lower() not in ("off", "none", "0", "false")


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
