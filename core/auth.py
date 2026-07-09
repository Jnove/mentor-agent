"""用户/验证码/会话 token 逻辑。纯 Python + SQLite，不依赖 streamlit。

密码 scrypt 哈希（每用户随机盐）；验证码只存 sha256；
会话为 HMAC 签名 token（user_id.过期时间.签名），无服务端会话表。
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from core.config import AUTH_DB

# 验证码
CODE_TTL = 600          # 10 分钟有效
RESEND_INTERVAL = 60    # 同邮箱重发间隔
MAX_CODE_ATTEMPTS = 5   # 错 5 次作废
# 登录锁定
LOCK_AFTER = 5
LOCK_SECONDS = 900

_SCRYPT = dict(n=2**14, r=8, p=1)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  status TEXT NOT NULL DEFAULT 'active',
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  last_login_at INTEGER
);
CREATE TABLE IF NOT EXISTS email_codes (
  email TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_sent_at INTEGER NOT NULL
);
"""


@contextmanager
def _connect(db_path: str | None = None):
    path = Path(db_path or AUTH_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        with db:  # 事务：正常提交，异常回滚
            yield db
    finally:
        db.close()


def init_db(db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.executescript(_SCHEMA)


# ---------- 邮箱与密码 ----------

def email_allowed(email: str, domains: list[str]) -> bool:
    email = email.strip().lower()
    if email.count("@") != 1:
        return False
    return email.rsplit("@", 1)[1] in domains


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"{salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt_hex, h_hex = stored.split("$", 1)
    h = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(h.hex(), h_hex)


# ---------- 用户 CRUD ----------

def create_user(email: str, password: str, role: str = "user",
                db_path: str | None = None, now: int | None = None) -> int:
    now = now or int(time.time())
    email = email.strip().lower()
    with _connect(db_path) as db:
        try:
            cur = db.execute(
                "INSERT INTO users (email, password_hash, role, created_at) VALUES (?,?,?,?)",
                (email, hash_password(password), role, now),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"邮箱已注册: {email}")
        return cur.lastrowid


def get_user(user_id: int, db_path: str | None = None) -> dict | None:
    with _connect(db_path) as db:
        row = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str, db_path: str | None = None) -> dict | None:
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT * FROM users WHERE email=?", (email.strip().lower(),)
        ).fetchone()
        return dict(row) if row else None


def list_users(db_path: str | None = None) -> list[dict]:
    with _connect(db_path) as db:
        rows = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]


def set_status(user_id: int, status: str, db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.execute("UPDATE users SET status=? WHERE id=?", (status, user_id))


def set_role(user_id: int, role: str, db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.execute("UPDATE users SET role=? WHERE id=?", (role, user_id))
