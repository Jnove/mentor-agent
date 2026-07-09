"""学长组 Agent 入口：页面导航（业务界面在 ui/，逻辑在 core/）。

用法:
    streamlit run app.py
"""
import streamlit as st

from ui.chat_page import render_chat

st.set_page_config(page_title="学长组 Agent", page_icon="🎓", layout="wide")

nav = st.navigation([st.Page(render_chat, title="问答", icon="🎓", default=True)])
nav.run()
