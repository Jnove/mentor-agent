"""core/auth 及配置测试（不依赖 streamlit/网络）。

用法: python tests/test_auth.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

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


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
