"""core/auth 及配置测试（不依赖 streamlit/网络）。

用法: python tests/test_auth.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import auth
from core.config import allowed_email_domains, auth_secret, session_days


def test_allowed_email_domains():
    os.environ.pop("ALLOWED_EMAIL_DOMAINS", None)
    assert allowed_email_domains() == ["zju.edu.cn"]
    os.environ["ALLOWED_EMAIL_DOMAINS"] = "@ZJU.edu.cn, cc98.org ,"
    assert allowed_email_domains() == ["zju.edu.cn", "cc98.org"]
    os.environ.pop("ALLOWED_EMAIL_DOMAINS", None)


def test_auth_secret():
    os.environ.pop("AUTH_SECRET", None)
    try:
        auth_secret()
        assert False, "缺 AUTH_SECRET 应报错"
    except RuntimeError:
        pass
    os.environ["AUTH_SECRET"] = "s3cret"
    assert auth_secret() == "s3cret"


def test_session_days():
    os.environ.pop("SESSION_DAYS", None)
    assert session_days() == 7
    os.environ["SESSION_DAYS"] = "3"
    assert session_days() == 3
    os.environ.pop("SESSION_DAYS", None)


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # 只要路径；auth 模块自己建库
    auth.init_db(path)
    return path


def test_email_allowed():
    domains = ["zju.edu.cn"]
    assert auth.email_allowed("a@zju.edu.cn", domains)
    assert auth.email_allowed("  A@ZJU.EDU.CN ", domains)
    assert not auth.email_allowed("a@qq.com", domains)
    assert not auth.email_allowed("azju.edu.cn", domains)
    assert not auth.email_allowed("a@fakezju.edu.cn", domains)


def test_password_roundtrip():
    stored = auth.hash_password("hunter2hunter2")
    assert auth.verify_password("hunter2hunter2", stored)
    assert not auth.verify_password("wrong-password", stored)
    # 同一密码两次哈希盐不同
    assert stored != auth.hash_password("hunter2hunter2")


def test_user_crud():
    db = _tmp_db()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)
    u = auth.get_user(uid, db_path=db)
    assert u["email"] == "a@zju.edu.cn" and u["role"] == "user" and u["status"] == "active"
    assert auth.get_user_by_email("a@zju.edu.cn", db_path=db)["id"] == uid
    assert auth.get_user_by_email("no@zju.edu.cn", db_path=db) is None
    try:
        auth.create_user("a@zju.edu.cn", "x" * 8, db_path=db)
        assert False, "重复邮箱应抛 ValueError"
    except ValueError:
        pass
    auth.set_role(uid, "admin", db_path=db)
    auth.set_status(uid, "disabled", db_path=db)
    u = auth.get_user(uid, db_path=db)
    assert u["role"] == "admin" and u["status"] == "disabled"
    assert len(auth.list_users(db_path=db)) == 1
    os.unlink(db)


def test_issue_and_verify_code():
    db = _tmp_db()
    t0 = 1_000_000
    code = auth.issue_code("a@zju.edu.cn", db_path=db, now=t0)
    assert code and len(code) == 6 and code.isdigit()
    # 60s 内重发被拒
    assert auth.issue_code("a@zju.edu.cn", db_path=db, now=t0 + 30) is None
    # 60s 后可重发（新码覆盖旧码）
    code2 = auth.issue_code("a@zju.edu.cn", db_path=db, now=t0 + 61)
    assert code2 is not None
    # 旧码失效、新码可用；用过即删，第二次验证失败
    assert not auth.verify_code("a@zju.edu.cn", code, db_path=db, now=t0 + 62) or code == code2
    assert auth.verify_code("a@zju.edu.cn", code2, db_path=db, now=t0 + 62)
    assert not auth.verify_code("a@zju.edu.cn", code2, db_path=db, now=t0 + 63)
    os.unlink(db)


def test_code_expiry_and_attempts():
    db = _tmp_db()
    t0 = 1_000_000
    code = auth.issue_code("a@zju.edu.cn", db_path=db, now=t0)
    # 过期
    assert not auth.verify_code("a@zju.edu.cn", code, db_path=db, now=t0 + 601)
    # 重新签发后连错 5 次作废，第 6 次即使码对也拒绝
    code = auth.issue_code("a@zju.edu.cn", db_path=db, now=t0 + 700)
    for _ in range(5):
        assert not auth.verify_code("a@zju.edu.cn", "000000" if code != "000000" else "111111",
                                    db_path=db, now=t0 + 701)
    assert not auth.verify_code("a@zju.edu.cn", code, db_path=db, now=t0 + 702)
    os.unlink(db)


def test_authenticate_and_lockout():
    db = _tmp_db()
    t0 = 1_000_000
    auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)

    status, user = auth.authenticate("a@zju.edu.cn", "hunter2hunter2", db_path=db, now=t0)
    assert status == "ok" and user["email"] == "a@zju.edu.cn"
    assert auth.get_user(user["id"], db_path=db)["last_login_at"] == t0

    # 不存在的邮箱与密码错误返回同一状态（防探测）
    assert auth.authenticate("no@zju.edu.cn", "x" * 8, db_path=db, now=t0)[0] == "bad_credentials"
    assert auth.authenticate("a@zju.edu.cn", "x" * 8, db_path=db, now=t0)[0] == "bad_credentials"

    # 连错 5 次锁定；锁定期内正确密码也拒绝；过期自动解锁
    for _ in range(4):  # 前面已错 1 次
        auth.authenticate("a@zju.edu.cn", "x" * 8, db_path=db, now=t0)
    assert auth.authenticate("a@zju.edu.cn", "hunter2hunter2", db_path=db, now=t0 + 1)[0] == "locked"
    assert auth.authenticate("a@zju.edu.cn", "hunter2hunter2", db_path=db, now=t0 + 901)[0] == "ok"

    # 禁用账号：密码对也拒绝
    auth.set_status(user["id"], "disabled", db_path=db)
    assert auth.authenticate("a@zju.edu.cn", "hunter2hunter2", db_path=db, now=t0 + 902)[0] == "disabled"
    os.unlink(db)


def test_token_roundtrip():
    t0 = 1_000_000
    token = auth.sign_token(42, t0 + 100, "secret")
    assert auth.verify_token(token, "secret", now=t0) == 42
    # 过期
    assert auth.verify_token(token, "secret", now=t0 + 101) is None
    # 篡改（换 uid / 换密钥 / 乱串）
    forged = "43" + token[2:]
    assert auth.verify_token(forged, "secret", now=t0) is None
    assert auth.verify_token(token, "other-secret", now=t0) is None
    assert auth.verify_token("garbage", "secret", now=t0) is None
    assert auth.verify_token("", "secret", now=t0) is None


def test_mailer_dev_mode():
    from core import mailer
    os.environ.pop("SMTP_HOST", None)
    assert not mailer.smtp_configured()
    # dev 模式不联网、不抛异常
    mailer.send_code("a@zju.edu.cn", "123456")
    os.environ["SMTP_HOST"] = "smtp.example.com"
    assert mailer.smtp_configured()
    os.environ.pop("SMTP_HOST", None)


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
