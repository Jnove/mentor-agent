"""学长组 Agent 入口：cookie 门禁 + 页面导航（界面在 ui/，逻辑在 core/）。

用法:
    streamlit run app.py
"""
import streamlit as st
from streamlit_cookies_controller import CookieController

from core import auth
from core.config import auth_secret
from ui.admin_page import render_admin
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
        # 同上：remove() 缺默认值会 KeyError，这里 token 存在理论上缓存里也有，但保持一致防护
        if controller.get(COOKIE_NAME):
            controller.remove(COOKIE_NAME)
    return None


def _logout() -> None:
    # 保留 CookieController 的缓存键（'cookies'）：清掉它会让下一轮 controller
    # 重新拉取浏览器 cookie 并触发 rerun，旧 token 又把人登回来。
    # 注意：这个字符串必须与 CookieController() 实例化时的 key 参数一致（当前用默认值 'cookies'）
    _cookies_cache = st.session_state.get("cookies")
    st.session_state.clear()
    if _cookies_cache is not None:
        st.session_state["cookies"] = _cookies_cache
    st.session_state.pending_cookie_clear = True  # 回调里组件不渲染，删除挪到下一轮


# 登出后的这一轮：渲染删除组件的同时必须跳过 cookie 门禁——
# controller.get 在本轮读到的还是服务端缓存的旧 token，会把人重新登进来
_logging_out = st.session_state.pop("pending_cookie_clear", False)
if _logging_out and controller.get(COOKIE_NAME):
    controller.remove(COOKIE_NAME)

user = None if _logging_out else current_user()
if user is None:
    render_auth(controller)
    st.stop()

# 上一轮 login_as 暂存的 cookie 在这一轮写入——本轮无 rerun，组件能完整渲染
if "pending_auth_cookie" in st.session_state:
    _token, _max_age = st.session_state.pop("pending_auth_cookie")
    controller.set(COOKIE_NAME, _token, max_age=_max_age, secure=True)

pages = [st.Page(render_chat, title="问答", icon="🎓", default=True)]
if user["role"] == "admin":
    pages.append(st.Page(render_admin, title="用户管理", icon="🔧"))
nav = st.navigation(pages)
with st.sidebar:
    st.caption(user["email"])
    st.button("退出登录", on_click=_logout, use_container_width=True)
nav.run()
