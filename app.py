"""学长组 Agent 入口：cookie 门禁 + 页面导航（界面在 ui/，逻辑在 core/）。

用法:
    streamlit run app.py
"""
import streamlit as st
from streamlit_cookies_controller import CookieController

from core import auth, usage
from core.config import PREWARM, auth_secret
from ui.admin_page import render_admin
from ui.auth_pages import COOKIE_NAME, render_auth
from ui.chat_page import _maybe_prewarm, render_chat
from ui.theme import apply_theme

st.set_page_config(page_title="学长组 Agent", page_icon="🎓", layout="wide")
apply_theme()

auth.init_db()
usage.init_db()
_secret = auth_secret()  # 缺 AUTH_SECRET 在这里就报错，不带病运行

if PREWARM:
    _maybe_prewarm()


def _known_cookies() -> dict[str, str]:
    """合并初始请求 cookie 与本会话里组件刚写入的最新缓存。"""
    cookies = st.context.cookies.to_dict()
    cached = st.session_state.get("cookies")
    if isinstance(cached, dict):
        cookies.update(cached)
    return cookies


def _cookie_controller() -> CookieController:
    """仅在写入/删除 cookie 时加载前端组件；普通刷新直接读请求 cookie。"""
    st.session_state.setdefault("cookies", _known_cookies())
    return CookieController()


def current_user() -> dict | None:
    """session_state 优先，其次 cookie；每次都复核用户仍 active（禁用立即生效）。"""
    # 注意：下面两个 if 必须顺序执行（不能改 elif）——session_state 里的用户失效后仍要走 cookie 分支完成清理
    if "user" in st.session_state:
        u = auth.get_user(st.session_state.user["id"])
        if u and u["status"] == "active":
            return st.session_state.user
        st.session_state.pop("user", None)
    # st.context 在 WebSocket 初始请求里就带有 cookie，避免 CookieController
    # 首次 getAll 的 iframe 往返和额外 rerun。
    token = None if st.session_state.get("auth_cookie_cleared") else _known_cookies().get(COOKIE_NAME)
    if token:
        uid = auth.verify_token(str(token), _secret)
        u = auth.get_user(uid) if uid else None
        if u and u["status"] == "active":
            st.session_state.user = {"id": u["id"], "email": u["email"], "role": u["role"]}
            return st.session_state.user
        # 同上：remove() 缺默认值会 KeyError，这里 token 存在理论上缓存里也有，但保持一致防护
        if _known_cookies().get(COOKIE_NAME):
            _cookie_controller().remove(COOKIE_NAME)
            st.session_state.auth_cookie_cleared = True
    return None


def _logout() -> None:
    _cookies_cache = _known_cookies()
    st.session_state.clear()
    st.session_state["cookies"] = _cookies_cache
    # st.context.cookies 在同一 WebSocket 会话中是初始快照；删除 cookie 后仍要
    # 显式忽略旧 token，直到用户重新登录或刷新建立新会话。
    st.session_state.auth_cookie_cleared = True
    st.session_state.pending_cookie_clear = True  # 回调里组件不渲染，删除挪到下一轮


@st.dialog("修改密码")
def change_password_dialog() -> None:
    u = st.session_state.user
    with st.form("change_pwd_form"):
        cur = st.text_input("当前密码", type="password")
        new = st.text_input("新密码（至少 8 位）", type="password")
        conf = st.text_input("确认新密码", type="password")
        if st.form_submit_button("确认修改", type="primary", width="stretch"):
            if new != conf:
                st.error("两次输入的新密码不一致")
            elif len(new) < 8:
                st.error("新密码至少 8 位")
            else:
                stored = auth.get_user(u["id"])["password_hash"]
                if not auth.verify_password(cur, stored):
                    st.error("当前密码错误")
                else:
                    auth.set_password(u["id"], new)
                    st.success("密码已修改")


# 登出后的这一轮：渲染删除组件的同时必须跳过 cookie 门禁——
# st.context 在本轮仍是旧 token，因此必须跳过门禁并删除浏览器 cookie
_logging_out = st.session_state.pop("pending_cookie_clear", False)
if _logging_out and _known_cookies().get(COOKIE_NAME):
    _cookie_controller().remove(COOKIE_NAME)

user = None if _logging_out else current_user()
if user is None:
    render_auth()
    st.stop()

# 上一轮 login_as 暂存的 cookie 在这一轮写入——本轮无 rerun，组件能完整渲染
if "pending_auth_cookie" in st.session_state:
    _token, _max_age = st.session_state.pop("pending_auth_cookie")
    _cookie_controller().set(COOKIE_NAME, _token, max_age=_max_age, secure=True)

pages = [st.Page(render_chat, title="问答", icon=":material/school:", default=True)]
if user["role"] == "admin":
    pages.append(st.Page(render_admin, title="用户管理", icon=":material/manage_accounts:"))
nav = st.navigation(pages)
with st.sidebar:
    st.caption(user["email"])
    if st.button("修改密码", width="stretch"):
        change_password_dialog()
    st.button("退出登录", on_click=_logout, width="stretch")
nav.run()
