"""使用埋点：提问日志 + 覆盖判定 + 统计查询。纯 Python + SQLite，不依赖 streamlit。

每条提问落一行，记录知识库命中片段数、最高重排分、是否触发百事通兜底、
是否"有据可答"（covered）以及用户反馈三态。管理页据此展示用户数与每日提问，
并列出知识库无法覆盖的问题。
"""
import sqlite3
import time
from contextlib import contextmanager

from core.config import USAGE_DB
from core.db import connect as _connect_raw


# 旧式 fallback：db_path 缺省走模块默认路径（同 auth.py 的注释）
@contextmanager
def _connect(db_path: str | None = None):
    with _connect_raw(db_path or USAGE_DB) as db:
        yield db

# feedback 三态：NULL 未反馈 / 0 没帮上 / 1 帮上了
FEEDBACK_UP = 1
FEEDBACK_DOWN = 0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,                -- 提问用户（auth.users.id）
  question TEXT NOT NULL,         -- 用户原话
  kind TEXT NOT NULL DEFAULT 'rag',   -- rag=政策 RAG 问答 / teacher=查老师卡片
  kb_hits INTEGER NOT NULL DEFAULT 0,   -- 知识库命中片段数（不含百事通）
  top_score REAL,                 -- 知识库最高重排分，reranker 不可用时为 NULL
  bst INTEGER NOT NULL DEFAULT 0, -- 是否触发百事通实时兜底
  covered INTEGER NOT NULL DEFAULT 0,   -- kb_hits>0 视为有据可答
  feedback INTEGER,               -- NULL 未反馈 / 0 没帮上 / 1 帮上了
  resolved INTEGER NOT NULL DEFAULT 0,  -- 管理员标记已处理
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_created ON questions(created_at);
"""


def init_db(db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.executescript(_SCHEMA)
        _migrate_kind(db)


def _migrate_kind(db) -> None:
    """旧库升级：为 questions 补 kind 列（历史记录均为 RAG 提问，默认 'rag'）。

    CREATE TABLE IF NOT EXISTS 不会给已存在的表加新列，升级前建的 usage.db
    没有 kind，管理页 uncovered 查询会报 no such column。
    """
    cols = {r[1] for r in db.execute("PRAGMA table_info(questions)").fetchall()}
    if "kind" not in cols:
        db.execute("ALTER TABLE questions ADD COLUMN kind TEXT NOT NULL DEFAULT 'rag'")


def log_question(user_id: int | None, question: str, kb_hits: int = 0,
                 top_score: float | None = None, bst: bool = False,
                 covered: bool = False, now: int | None = None,
                 kind: str = "rag", db_path: str | None = None) -> int:
    """记录一次提问，返回日志 id（供反馈按钮回查）。

    kind 区分链路：rag=政策问答（参与未覆盖统计），teacher=查老师卡片（不参与）。
    """
    now = int(time.time()) if now is None else now
    with _connect(db_path) as db:
        cur = db.execute(
            "INSERT INTO questions (user_id, question, kind, kb_hits, top_score, bst, covered, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (user_id, question, kind, kb_hits, top_score, int(bst), int(covered), now),
        )
        return cur.lastrowid


def set_feedback(log_id: int, feedback: int, db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.execute("UPDATE questions SET feedback=? WHERE id=?", (feedback, log_id))


def set_resolved(log_id: int, resolved: bool = True, db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.execute("UPDATE questions SET resolved=? WHERE id=?",
                   (1 if resolved else 0, log_id))


def _uncovered_cond() -> str:
    """未覆盖 = 库外（covered=0）或 用户明确反馈没帮上（feedback=0）；仅统计 RAG 链路。

    老师卡片（kind='teacher'）自成功能，不混进"知识库未覆盖"——那是政策问答的度量。
    """
    return "(kind='rag' AND (covered=0 OR feedback=0))"


def stats_summary(now: int | None = None, db_path: str | None = None) -> dict:
    """今日与累计的提问/未覆盖数；活跃用户数由 auth 提供，不在这里。"""
    now = int(time.time()) if now is None else now
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    cond = _uncovered_cond()
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT COUNT(*) AS total, "
            f"COALESCE(SUM(CASE WHEN {cond} THEN 1 ELSE 0 END), 0) AS uncovered "
            "FROM questions WHERE date(created_at,'unixepoch','localtime')=?",
            (today,),
        ).fetchone()
        total_all = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        uncovered_all = db.execute(
            f"SELECT COUNT(*) FROM questions WHERE {cond}"
        ).fetchone()[0]
    return {
        "today_total": row["total"],
        "today_uncovered": row["uncovered"],
        "total_all": total_all,
        "uncovered_all": uncovered_all,
    }


def daily_counts(days: int = 7, now: int | None = None,
                 db_path: str | None = None) -> list[dict]:
    """最近 days 个自然日（含今天）的 {date,total,uncovered,dau}，升序，缺日补 0。

    uncovered 按提问当天是否 covered=0 或之后被反馈没帮上计，不随 resolved 回退。
    """
    now = int(time.time()) if now is None else now
    cutoff = now - (days - 1) * 86400
    cond = _uncovered_cond()
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT date(created_at,'unixepoch','localtime') AS d, "
            "COUNT(*) AS total, "
            f"COALESCE(SUM(CASE WHEN {cond} THEN 1 ELSE 0 END), 0) AS uncovered, "
            "COUNT(DISTINCT user_id) AS dau "
            "FROM questions WHERE created_at>=? GROUP BY d ORDER BY d",
            (cutoff,),
        ).fetchall()
    by_date = {r["d"]: r for r in rows}
    out = []
    for i in range(days - 1, -1, -1):
        d = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
        r = by_date.get(d)
        out.append({
            "date": d,
            "total": r["total"] if r else 0,
            "uncovered": r["uncovered"] if r else 0,
            "dau": r["dau"] if r else 0,
        })
    return out


def uncovered_rows(limit: int = 100, include_resolved: bool = False,
                   db_path: str | None = None) -> list[dict]:
    """未覆盖问题明细，新→旧。默认不含已处理，include_resolved=True 时全量。"""
    where = f"WHERE {_uncovered_cond()}"
    if not include_resolved:
        where += " AND resolved=0"
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT id, user_id, question, kb_hits, top_score, bst, covered, "
            "feedback, resolved, created_at FROM questions "
            f"{where} ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
