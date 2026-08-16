"""管理后台页：用户管理 + 使用统计（仅 admin 导航可见）。"""
import time

import streamlit as st

from core import auth, usage


def _fmt(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def _page_state(key: str, total: int, size: int) -> tuple[int, int, int]:
    pages = max(1, (total + size - 1) // size)
    page = min(st.session_state.get(key, 1), pages)
    st.session_state[key] = page
    return page, pages, (page - 1) * size


def _page_size(key: str) -> int:
    return st.selectbox("每页", [15, 20, 30, 50], index=1, key=f"{key}_size")


def _page_bar(key: str, page: int, pages: int, total: int) -> None:
    c_prev, c_mid, c_next = st.columns([1, 2, 1])
    with c_prev:
        if st.button("上一页", key=f"{key}_prev", disabled=page <= 1):
            st.session_state[key] = page - 1
            st.rerun()
    with c_mid:
        st.caption(f"第 {page} / {pages} 页 · 共 {total} 条")
    with c_next:
        if st.button("下一页", key=f"{key}_next", disabled=page >= pages):
            st.session_state[key] = page + 1
            st.rerun()


def render_admin() -> None:
    st.markdown(
        '<div class="panel-title"><span class="bar bar-bronze"></span>管理后台</div>',
        unsafe_allow_html=True,
    )
    tab_users, tab_stats = st.tabs(["用户管理", "使用统计"])
    with tab_users:
        _render_users()
    with tab_stats:
        _render_stats()


def _render_users() -> None:
    me = st.session_state.user
    users = auth.list_users()
    size = _page_size("users")
    page, pages, start = _page_state("users_page", len(users), size)
    for u in users[start:start + size]:
        is_me = u["id"] == me["id"]
        c_info, c_btn = st.columns([7, 3])
        c_info.markdown(
            f"**{u['email']}**{'（我）' if is_me else ''} · 角色 {u['role']} · 状态 {u['status']}"
            f" · 注册 {_fmt(u['created_at'])} · 登录 {_fmt(u['last_login_at'])}"
        )
        with c_btn:
            b1, b2 = st.columns(2)
            with b1:
                if u["status"] == "active":
                    if st.button("禁用", key=f"dis_{u['id']}", disabled=is_me):
                        auth.set_status(u["id"], "disabled")
                        st.rerun()
                else:
                    if st.button("启用", key=f"ena_{u['id']}"):
                        auth.set_status(u["id"], "active")
                        st.rerun()
            with b2:
                if u["role"] == "user":
                    if st.button("升管理员", key=f"adm_{u['id']}"):
                        auth.set_role(u["id"], "admin")
                        st.rerun()
                else:
                    if st.button("撤管理员", key=f"usr_{u['id']}", disabled=is_me):
                        auth.set_role(u["id"], "user")
                        st.rerun()
        st.divider()
    _page_bar("users_page", page, pages, len(users))


def _render_stats() -> None:
    summary = usage.stats_summary()
    active = auth.count_users("active")
    rate = summary["uncovered_all"] / summary["total_all"] if summary["total_all"] else 0
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("活跃用户数", active)
    c2.metric("今日提问数", summary["today_total"])
    c3.metric("今日未覆盖", summary["today_uncovered"])
    c4.metric("累计未覆盖率", f"{rate:.0%}")

    st.subheader("近 7 天每日提问")
    rows = [
        {"日期": r["date"], "提问数": r["total"], "未覆盖数": r["uncovered"], "活跃用户数": r["dau"]}
        for r in usage.daily_counts(7)
    ]
    st.dataframe(rows, width="stretch")

    st.subheader("未覆盖问题")
    show_resolved = st.checkbox("显示已处理", value=False)
    questions = usage.uncovered_rows(limit=1000, include_resolved=show_resolved)
    emails = {u["id"]: u["email"] for u in auth.list_users()}
    size = _page_size("uq")
    page, pages, start = _page_state("uq_page", len(questions), size)
    if not questions:
        st.caption("暂无未覆盖问题")
    for q in questions[start:start + size]:
        who = emails.get(q["user_id"], f"用户 #{q['user_id']}")
        top = f"{q['top_score']:.2f}" if q["top_score"] is not None else "—"
        parts = [f"{_fmt(q['created_at'])} · {who}"]
        parts.append(f"知识库命中 {q['kb_hits']} 条（相关度 {top}）" if q["kb_hits"] else "知识库未命中")
        if q["bst"]:
            parts.append("已用百事通兜底")
        if q["feedback"] == usage.FEEDBACK_DOWN:
            parts.append("用户反馈没帮上")
        elif q["feedback"] == usage.FEEDBACK_UP:
            parts.append("用户反馈已解决")
        c_info, c_act = st.columns([8, 1])
        c_info.markdown(f"**{q['question']}**")
        c_info.caption(" · ".join(parts))
        if c_act.button("标记处理", key=f"res_{q['id']}"):
            usage.set_resolved(q["id"])
            st.rerun()
        st.divider()

    _page_bar("uq_page", page, pages, len(questions))
