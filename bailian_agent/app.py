"""Minimal Streamlit UI backed entirely by Alibaba Cloud Model Studio."""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from bailian_agent.client import BailianClient, BailianError


load_dotenv(Path(__file__).with_name(".env"))

st.set_page_config(page_title="百炼校园政策问答", page_icon="🎓")
st.title("校园政策问答")
st.caption("检索与回答由阿里云百炼知识库应用完成")


def show_references(references: list[dict]) -> None:
    if not references:
        return
    with st.expander(f"参考资料（{len(references)}）"):
        for reference in references:
            title = reference.get("title") or reference.get("doc_name") or "未命名文档"
            url = reference.get("doc_url") or reference.get("url")
            st.markdown(f"[**{title}**]({url})" if url else f"**{title}**")
            if reference.get("text"):
                st.caption(reference["text"][:500])


api_key = os.getenv("DASHSCOPE_API_KEY", "")
app_id = os.getenv("BAILIAN_APP_ID", "")

if not api_key or not app_id:
    st.error("请在 bailian_agent/.env 中配置 DASHSCOPE_API_KEY 和 BAILIAN_APP_ID。")
    st.stop()


@st.cache_resource
def get_client() -> BailianClient:
    return BailianClient(
        api_key,
        app_id,
        base_url=os.getenv("BAILIAN_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"),
        workspace_id=os.getenv("BAILIAN_WORKSPACE_ID", ""),
    )


if "messages" not in st.session_state:
    st.session_state.messages = []
if "bailian_session_id" not in st.session_state:
    st.session_state.bailian_session_id = None

if st.sidebar.button("清空对话", width="stretch"):
    st.session_state.messages = []
    st.session_state.bailian_session_id = None
    st.rerun()

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        show_references(message.get("references", []))

if question := st.chat_input("请输入学校政策问题"):
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🎓"):
        try:
            with st.spinner("正在查询知识库..."):
                result = get_client().chat(question, st.session_state.bailian_session_id)
            st.markdown(result["text"])
            show_references(result["references"])
            st.session_state.bailian_session_id = result["session_id"]
            st.session_state.messages.append(
                {
                    "role": "assistant",
                    "content": result["text"],
                    "references": result["references"],
                }
            )
        except BailianError as exc:
            st.error(str(exc))
            st.session_state.messages.pop()
