"""学长组 Agent 入口：cookie 门禁 + 页面导航（界面在 ui/，逻辑在 core/）。

用法:
    streamlit run app.py
"""
import streamlit as st
from streamlit_cookies_controller import CookieController

from core import auth
from core.config import auth_secret
from ui.auth_pages import COOKIE_NAME, render_auth
from ui.chat_page import render_chat

st.set_page_config(page_title="学长组 Agent", page_icon="🎓", layout="wide")

auth.init_db()
_secret = auth_secret()  # 缺 AUTH_SECRET 在这里就报错，不带病运行
controller = CookieController()


def current_user() -> dict | None:
    """session_state 优先，其次 cookie；每次都复核用户仍 active（禁用立即生效）。"""
    # 注意：下面两个 if 必须顺序执行（不能改 elif）——session_state 里的用户失效后仍要走 cookie 分支完成清理
    if "user" in st.session_state:
        u = auth.get_user(st.session_state.user["id"])
        if u and u["status"] == "active":
            return st.session_state.user
        st.session_state.pop("user", None)
    token = controller.get(COOKIE_NAME)
    if token:
        uid = auth.verify_token(str(token), _secret)
        u = auth.get_user(uid) if uid else None
        if u and u["status"] == "active":
            st.session_state.user = {"id": u["id"], "email": u["email"], "role": u["role"]}
            return st.session_state.user
        controller.remove(COOKIE_NAME)
    return None


def _logout() -> None:
    controller.remove(COOKIE_NAME)
    st.session_state.clear()


user = current_user()
if user is None:
    render_auth(controller)
    st.stop()

pages = [st.Page(render_chat, title="问答", icon="🎓", default=True)]
nav = st.navigation(pages)
with st.sidebar:
    st.caption(user["email"])
    st.button("退出登录", on_click=_logout, use_container_width=True)
nav.run()
