"""登录 / 注册页。业务规则全部在 core.auth，这里只做表单和提示。"""
import time

import streamlit as st

from core import auth
from core.config import allowed_email_domains, auth_secret, session_days
from core.mailer import send_code, smtp_configured

COOKIE_NAME = "mentor_auth"


def login_as(user: dict, controller) -> None:
    """签 token 写 cookie，并把用户挂到 session_state。"""
    days = session_days()
    token = auth.sign_token(user["id"], int(time.time()) + days * 86400, auth_secret())
    controller.set(COOKIE_NAME, token, max_age=days * 86400)
    st.session_state.user = {"id": user["id"], "email": user["email"], "role": user["role"]}


def render_auth(controller) -> None:
    st.markdown("## 学长组 Agent · 登录")
    if not smtp_configured():
        st.warning("开发模式：SMTP 未配置，验证码会打印在服务器控制台。")
    tab_login, tab_register = st.tabs(["登录", "注册"])
    with tab_login:
        _render_login(controller)
    with tab_register:
        _render_register(controller)


def _render_login(controller) -> None:
    with st.form("login_form"):
        email = st.text_input("邮箱")
        password = st.text_input("密码", type="password")
        if st.form_submit_button("登录", use_container_width=True):
            status, user = auth.authenticate(email.strip().lower(), password)
            if status == "ok":
                login_as(user, controller)
                st.rerun()
            elif status == "locked":
                st.error("失败次数过多，账号已锁定 15 分钟")
            elif status == "disabled":
                st.error("账号已被禁用，请联系管理员")
            else:
                st.error("邮箱或密码错误")


def _render_register(controller) -> None:
    step = st.session_state.setdefault("reg_step", 1)
    if step == 1:
        with st.form("reg_email_form"):
            email = st.text_input("邮箱（仅限校内邮箱）")
            if st.form_submit_button("发送验证码", use_container_width=True):
                email = email.strip().lower()
                domains = allowed_email_domains()
                if not auth.email_allowed(email, domains):
                    st.error("仅允许以下后缀注册：" + "、".join("@" + d for d in domains))
                elif auth.get_user_by_email(email):
                    st.error("该邮箱已注册，请直接登录")
                else:
                    code = auth.issue_code(email)
                    if code is None:
                        st.error("发送过于频繁，请 60 秒后再试")
                    else:
                        try:
                            send_code(email, code)
                        except Exception:
                            st.error("邮件发送失败，请稍后再试")
                            return
                        st.session_state.reg_email = email
                        st.session_state.reg_step = 2
                        st.rerun()
    else:
        st.caption(f"验证码已发送至 {st.session_state.reg_email}（10 分钟内有效）")
        with st.form("reg_code_form"):
            code = st.text_input("6 位验证码")
            password = st.text_input("设置密码（至少 8 位）", type="password")
            if st.form_submit_button("完成注册", use_container_width=True):
                if len(password) < 8:
                    st.error("密码至少 8 位")
                elif not auth.verify_code(st.session_state.reg_email, code):
                    st.error("验证码错误或已失效")
                else:
                    uid = auth.create_user(st.session_state.reg_email, password)
                    st.session_state.pop("reg_step", None)
                    st.session_state.pop("reg_email", None)
                    login_as(auth.get_user(uid), controller)
                    st.rerun()
        if st.button("换个邮箱重新发送"):
            st.session_state.reg_step = 1
            st.rerun()
