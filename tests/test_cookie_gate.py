"""app.py cookie 登录状态机回归测试（AppTest 驱动，浏览器手测曾在这里连修 3 个 bug）。

锁定的时序契约（对应提交 8935370 / aea2802 / 65bf897）：
1. 登录：login_as 只把 token 暂存 pending_auth_cookie，controller.set 必须发生在
   st.rerun 之后、门禁通过的那一轮（rerun 会清掉本轮元素，组件来不及写浏览器）
2. 登出：_logout 保留 CookieController 缓存键 'cookies'，下一轮跳过门禁并 remove
3. current_user 两个 if 顺序执行（session 用户失效后仍要走 cookie 分支完成清理）

CookieController 是前端组件，这里用 FakeCookieController 按 run 粒度模拟其
时序语义（见类 docstring），并通过 sys.modules 注入替换真组件。

用法: python tests/test_cookie_gate.py
"""
import os
import sys
import tempfile
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from streamlit.testing.v1 import AppTest

from core import auth

SECRET = "test-secret-for-apptest"
APP = str(Path(__file__).parent.parent / "app.py")
COOKIE = "mentor_auth"


class FakeCookieController:
    """模拟 streamlit_cookies_controller.CookieController 的 run 粒度时序语义。

    - browser：类变量，扮演浏览器 cookie jar，跨 run、跨会话（跨 AppTest 实例）存活
    - 首次挂载（session_state 无缓存键）：本轮 get 只能读到 default={}，浏览器值
      写入缓存、下一轮才可见——对应真组件"前端回传 getAll 结果再触发 rerun"的一轮延迟
    - set/remove 同真组件：立即改 session 缓存 + 浏览器；remove 缓存缺键时 KeyError
    - calls 记录每次调用发生在第几轮脚本运行（run 序号），供断言写/删的时点
    局限：首次挂载轮内紧接着 set 的场景没有模拟（app.py 的状态机不会出现）。
    """
    browser: dict = {}
    calls: list = []          # (op, name, value, options, run_no)
    run_count = 0             # app.py 每轮顶层实例化一次，因此等于脚本运行轮数

    @classmethod
    def reset(cls, browser: dict | None = None) -> None:
        cls.browser = dict(browser or {})
        cls.calls = []
        cls.run_count = 0

    def __init__(self, key: str = "cookies"):
        cls = FakeCookieController
        cls.run_count += 1
        if key not in st.session_state:
            self._cookies = {}                             # 真组件首轮返回 default={}
            st.session_state[key] = dict(cls.browser)      # 前端回传，下一轮才可见
        else:
            self._cookies = st.session_state[key]
            st.session_state[key] = self._cookies

    def get(self, name):
        return self._cookies.get(name)

    def getAll(self):
        return self._cookies

    def set(self, name, value, **options):
        cls = FakeCookieController
        cls.calls.append(("set", name, value, options, cls.run_count))
        self._cookies[name] = value
        cls.browser[name] = value

    def remove(self, name, **options):
        cls = FakeCookieController
        cls.calls.append(("remove", name, None, options, cls.run_count))
        self._cookies.pop(name)  # 真组件缓存缺键就是 KeyError，特意保留
        cls.browser.pop(name, None)


# app.py 顶层 `from streamlit_cookies_controller import CookieController`：注入假模块
_fake_ccm = types.ModuleType("streamlit_cookies_controller")
_fake_ccm.CookieController = FakeCookieController
sys.modules["streamlit_cookies_controller"] = _fake_ccm

# ui.chat_page 顶层会 import chromadb / embedding 等重资源，且 render_chat 需要
# LLM_API_KEY 与知识库——与 cookie 门禁无关，整个替换成轻量桩
import ui  # noqa: E402

_fake_chat = types.ModuleType("ui.chat_page")


def _render_chat_stub() -> None:
    st.markdown("chat-stub")


_fake_chat.render_chat = _render_chat_stub
sys.modules["ui.chat_page"] = _fake_chat
ui.chat_page = _fake_chat

os.environ["AUTH_SECRET"] = SECRET
os.environ.pop("SESSION_DAYS", None)


# ---------- 工具 ----------

def _new_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    auth.AUTH_DB = path  # app.py 全走默认 db_path，改模块全局即可重定向
    auth.init_db(path)
    return path


def _cleanup_db(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(path + suffix).unlink(missing_ok=True)


def _valid_token(uid: int, seconds: int = 86400) -> str:
    return auth.sign_token(uid, int(time.time()) + seconds, SECRET)


def _run(at: AppTest) -> AppTest:
    at.run()
    assert not at.exception, f"脚本运行抛异常: {[e.value for e in at.exception]}"
    return at


def _button(at: AppTest, label: str):
    matches = [b for b in at.button if b.label == label]
    assert len(matches) == 1, f"按钮 {label!r} 应唯一，实际: {[b.label for b in at.button]}"
    return matches[0]


def _text_input(at: AppTest, label: str):
    matches = [t for t in at.text_input if t.label == label]
    assert len(matches) == 1, f"输入框 {label!r} 应唯一，实际: {[t.label for t in at.text_input]}"
    return matches[0]


def _on_login_page(at: AppTest) -> bool:
    return any(b.label == "登录" for b in at.button)


def _logged_in(at: AppTest) -> bool:
    return any(b.label == "退出登录" for b in at.button)


def _has_state(at: AppTest, key: str) -> bool:
    try:
        at.session_state[key]
        return True
    except KeyError:
        return False


def _login_via_cookie(at: AppTest) -> AppTest:
    """浏览器带着有效 cookie 打开：第 1 轮组件挂载拉取 cookie，第 2 轮完成自动登录。"""
    _run(at)
    assert _on_login_page(at), "首轮 cookie 尚未回传，应短暂停在登录页"
    _run(at)  # 模拟前端回传 getAll 结果后触发的 rerun
    assert _logged_in(at), "第 2 轮应凭 cookie 自动登录"
    return at


# ---------- 契约 1：登录写 cookie 延迟到 rerun 后（8935370） ----------

def test_login_sets_cookie_after_rerun_not_before():
    db = _new_db()
    FakeCookieController.reset()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)

    at = AppTest.from_file(APP, default_timeout=10)
    _run(at)
    assert _on_login_page(at) and not _has_state(at, "user")

    _text_input(at, "邮箱").set_value("a@zju.edu.cn")
    _text_input(at, "密码").set_value("hunter2hunter2")
    _button(at, "登录").click()
    _run(at)  # 提交轮（run 2）→ login_as 暂存 → st.rerun → 门禁后写入轮（run 3）

    assert _logged_in(at) and at.session_state["user"]["id"] == uid
    assert not _has_state(at, "pending_auth_cookie"), "暂存的 token 应在写入轮被消费"

    sets = [c for c in FakeCookieController.calls if c[0] == "set"]
    assert len(sets) == 1, f"cookie 应恰好写一次: {FakeCookieController.calls}"
    _, name, token, options, run_no = sets[0]
    assert name == COOKIE
    assert FakeCookieController.run_count == 3, "登录应是 初始→提交→rerun 共 3 轮"
    assert run_no == 3, (
        "controller.set 必须发生在 rerun 之后的门禁通过轮；"
        "在 login_as 里直接 set 会被 rerun 清掉元素、写不进浏览器"
    )
    assert options.get("secure") is True
    assert options.get("max_age") == 7 * 86400
    assert auth.verify_token(FakeCookieController.browser[COOKIE], SECRET) == uid

    _cleanup_db(db)


# ---------- cookie 自动登录（7 天保持的回归面） ----------

def test_cookie_auto_login():
    db = _new_db()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)
    FakeCookieController.reset({COOKIE: _valid_token(uid)})

    at = _login_via_cookie(AppTest.from_file(APP, default_timeout=10))
    assert at.session_state["user"]["id"] == uid
    assert FakeCookieController.calls == [], "自动登录不应写/删 cookie"
    assert COOKIE in FakeCookieController.browser

    _cleanup_db(db)


def test_invalid_cookie_cleaned_and_stays_out():
    db = _new_db()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)
    # 过期 token：验签失败 → 清 cookie 回登录页
    FakeCookieController.reset({COOKIE: auth.sign_token(uid, int(time.time()) - 1, SECRET)})

    at = AppTest.from_file(APP, default_timeout=10)
    _run(at)
    _run(at)  # cookie 回传轮
    assert _on_login_page(at) and not _has_state(at, "user")
    removes = [c for c in FakeCookieController.calls if c[0] == "remove"]
    assert len(removes) == 1 and removes[0][1] == COOKIE
    assert COOKIE not in FakeCookieController.browser, "失效 cookie 应从浏览器删除"

    _cleanup_db(db)


def test_disabled_user_cookie_rejected():
    db = _new_db()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)
    auth.set_status(uid, "disabled", db_path=db)
    FakeCookieController.reset({COOKIE: _valid_token(uid)})

    at = AppTest.from_file(APP, default_timeout=10)
    _run(at)
    _run(at)
    assert _on_login_page(at) and not _has_state(at, "user"), "禁用应立即生效"
    assert COOKIE not in FakeCookieController.browser

    _cleanup_db(db)


# ---------- 契约 3：current_user 两个 if 顺序执行、不能改 elif ----------

def test_stale_session_user_still_cleans_cookie_same_run():
    """session 里的用户被禁用后，同一轮必须继续走 cookie 分支把失效 cookie 删掉。

    若 if→elif：本轮走完 session 分支就返回，remove 不会发生，浏览器里留着
    失效 token（下轮才清），此处的单轮断言会失败。
    """
    db = _new_db()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)
    FakeCookieController.reset({COOKIE: _valid_token(uid)})

    at = _login_via_cookie(AppTest.from_file(APP, default_timeout=10))
    auth.set_status(uid, "disabled", db_path=db)  # 登录后管理员禁用该账号
    FakeCookieController.calls = []
    run_before = FakeCookieController.run_count

    _run(at)  # 单轮：session 分支失效 → 落入 cookie 分支 → remove
    assert _on_login_page(at) and not _has_state(at, "user")
    removes = [c for c in FakeCookieController.calls if c[0] == "remove"]
    assert len(removes) == 1 and removes[0][4] == run_before + 1, (
        "session 用户失效的同一轮就要清掉 cookie（两个 if 必须顺序执行，不能 elif）"
    )
    assert COOKIE not in FakeCookieController.browser

    _cleanup_db(db)


# ---------- 契约 2：登出（aea2802 + 65bf897） ----------

def test_logout_clears_cookie_and_stays_logged_out():
    db = _new_db()
    uid = auth.create_user("a@zju.edu.cn", "hunter2hunter2", db_path=db)
    FakeCookieController.reset({COOKIE: _valid_token(uid)})

    at = _login_via_cookie(AppTest.from_file(APP, default_timeout=10))
    at.session_state["notes"] = ["某轮问答的笔记"]  # 充当登出要清空的业务状态

    _button(at, "退出登录").click()
    _run(at)  # 登出轮：回调清 session（留 cookies 键）→ 本轮 remove + 强制未登录

    assert _on_login_page(at) and not _has_state(at, "user")
    assert not _has_state(at, "notes"), "登出应清空业务 session 状态"
    assert _has_state(at, "cookies"), (
        "CookieController 缓存键必须保留：清掉它会让组件重新拉取浏览器 cookie，"
        "登出轮 get 读到空 default、remove 被跳过，旧 token 又把人登回来"
    )
    removes = [c for c in FakeCookieController.calls if c[0] == "remove"]
    assert len(removes) == 1 and removes[0][4] == FakeCookieController.run_count, (
        "remove 必须发生在登出后的下一轮（回调里组件不渲染，删不动浏览器）"
    )
    assert COOKIE not in FakeCookieController.browser
    assert not _has_state(at, "pending_cookie_clear"), "登出标记应一次性消费"

    # 同一会话再跑一轮：旧 token 不复活
    _run(at)
    assert _on_login_page(at) and not _has_state(at, "user")

    # 新会话（浏览器重开/刷新，共享 jar）：确认浏览器 cookie 真的删掉了
    at2 = AppTest.from_file(APP, default_timeout=10)
    _run(at2)
    _run(at2)
    assert _on_login_page(at2) and not _has_state(at2, "user"), "登出后刷新不应自动登录"

    _cleanup_db(db)


def test_pending_clear_without_cached_cookie_no_crash():
    """登出轮缓存里没有该 cookie 时不能崩：remove() 缓存缺键会 KeyError，必须有 get 防护。"""
    db = _new_db()
    FakeCookieController.reset()

    at = AppTest.from_file(APP, default_timeout=10)
    at.session_state["cookies"] = {}
    at.session_state["pending_cookie_clear"] = True
    _run(at)  # 无防护会在 controller.remove 处 KeyError，被 _run 的断言捕获

    assert _on_login_page(at) and not _has_state(at, "user")
    assert FakeCookieController.calls == []

    _cleanup_db(db)


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
