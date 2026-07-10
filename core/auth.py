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

from core.config import AUTH_DB, admin_emails

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
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    now = int(time.time()) if now is None else now
    email = email.strip().lower()
    # .env 的 ADMIN_EMAILS 名单：注册即管理员（显式传 role 的调用方优先）
    if role == "user" and email in admin_emails():
        role = "admin"
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


# ---------- 邮箱验证码 ----------

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def issue_code(email: str, db_path: str | None = None, now: int | None = None) -> str | None:
    """签发 6 位验证码；同邮箱 60s 内重复请求返回 None。"""
    now = int(time.time()) if now is None else now
    email = email.strip().lower()
    code = f"{secrets.randbelow(10**6):06d}"
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT last_sent_at FROM email_codes WHERE email=?", (email,)
        ).fetchone()
        if row and now - row["last_sent_at"] < RESEND_INTERVAL:
            return None
        db.execute(
            "REPLACE INTO email_codes (email, code_hash, expires_at, attempts, last_sent_at) "
            "VALUES (?,?,?,0,?)",
            (email, _sha256(code), now + CODE_TTL, now),
        )
    return code


def verify_code(email: str, code: str, db_path: str | None = None,
                now: int | None = None) -> bool:
    """校验验证码：成功即删（一次性）；失败计次，达上限作废。"""
    now = int(time.time()) if now is None else now
    email = email.strip().lower()
    with _connect(db_path) as db:
        row = db.execute("SELECT * FROM email_codes WHERE email=?", (email,)).fetchone()
        if not row or now > row["expires_at"] or row["attempts"] >= MAX_CODE_ATTEMPTS:
            return False
        if hmac.compare_digest(row["code_hash"], _sha256(code.strip())):
            db.execute("DELETE FROM email_codes WHERE email=?", (email,))
            return True
        db.execute("UPDATE email_codes SET attempts=attempts+1 WHERE email=?", (email,))
        return False


def discard_code(email: str, db_path: str | None = None) -> None:
    """作废验证码（邮件发送失败时回滚，避免 60s 重发窗口锁死）。"""
    with _connect(db_path) as db:
        db.execute("DELETE FROM email_codes WHERE email=?", (email.strip().lower(),))


# ---------- 登录 ----------

def authenticate(email: str, password: str, db_path: str | None = None,
                 now: int | None = None) -> tuple[str, dict | None]:
    """返回 (状态, 用户)。状态: ok / bad_credentials / locked / disabled。

    先验密码再看 disabled，避免用响应差异探测邮箱是否已注册。
    """
    now = int(time.time()) if now is None else now
    user = get_user_by_email(email, db_path=db_path)
    if not user:
        return "bad_credentials", None
    if user["locked_until"] > now:
        return "locked", None
    if not verify_password(password, user["password_hash"]):
        failed = user["failed_logins"] + 1
        locked_until = now + LOCK_SECONDS if failed >= LOCK_AFTER else 0
        with _connect(db_path) as db:
            db.execute(
                "UPDATE users SET failed_logins=?, locked_until=? WHERE id=?",
                (0 if locked_until else failed, locked_until, user["id"]),
            )
        return "bad_credentials", None
    if user["status"] != "active":
        return "disabled", None
    with _connect(db_path) as db:
        db.execute(
            "UPDATE users SET failed_logins=0, locked_until=0, last_login_at=? WHERE id=?",
            (now, user["id"]),
        )
    # ADMIN_EMAILS 里的老账号在这里补提升；只升不降，撤销走管理页
    if user["role"] != "admin" and user["email"] in admin_emails():
        set_role(user["id"], "admin", db_path=db_path)
    return "ok", get_user(user["id"], db_path=db_path)


# ---------- 会话 token（HMAC 签名，无服务端状态） ----------

def sign_token(user_id: int, expires_at: int, secret: str) -> str:
    payload = f"{user_id}.{expires_at}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_token(token: str, secret: str, now: int | None = None) -> int | None:
    """验签 + 验过期，通过返回 user_id，否则 None。"""
    now = int(time.time()) if now is None else now
    try:
        uid_s, exp_s, sig = token.split(".")
        payload = f"{uid_s}.{exp_s}"
        expect = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expect) or int(exp_s) < now:
            return None
        return int(uid_s)
    except (ValueError, AttributeError):
        return None
