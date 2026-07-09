"""用户管理页（仅 admin 导航可见；操作自己被禁止）。"""
import time

import streamlit as st

from core import auth


def _fmt(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def render_admin() -> None:
    st.markdown("### 用户管理")
    me = st.session_state.user
    for u in auth.list_users():
        is_me = u["id"] == me["id"]
        c_info, c_status, c_role = st.columns([5, 2, 2])
        c_info.markdown(
            f"**{u['email']}**{'（我）' if is_me else ''}  \n"
            f"角色 {u['role']} · 状态 {u['status']} · 注册 {_fmt(u['created_at'])}"
            f" · 最后登录 {_fmt(u['last_login_at'])}"
        )
        if u["status"] == "active":
            if c_status.button("禁用", key=f"dis_{u['id']}", disabled=is_me):
                auth.set_status(u["id"], "disabled")
                st.rerun()
        else:
            if c_status.button("启用", key=f"ena_{u['id']}"):
                auth.set_status(u["id"], "active")
                st.rerun()
        if u["role"] == "user":
            if c_role.button("设为管理员", key=f"adm_{u['id']}"):
                auth.set_role(u["id"], "admin")
                st.rerun()
        else:
            if c_role.button("撤销管理员", key=f"usr_{u['id']}", disabled=is_me):
                auth.set_role(u["id"], "user")
                st.rerun()
        st.divider()
