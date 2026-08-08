"""Minimal Streamlit UI backed by Alibaba Cloud Model Studio Knowledge Chat."""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from bailian_agent.client import BailianClient, BailianError, load_exported_credentials


load_dotenv(Path(__file__).with_name(".env"))

st.set_page_config(page_title="百炼校园政策问答", page_icon="🎓")
st.title("校园政策问答")
st.caption("检索与回答由阿里云百炼知识库完成")


def show_references(references: list[dict]) -> None:
    if not references:
        return
    with st.expander(f"参考资料（{len(references)}）"):
        for reference in references:
            title = f"[{reference['index']}] {reference['title']}"
            url = reference.get("doc_url")
            st.markdown(f"[**{title}**]({url})" if url else f"**{title}**")
            if reference.get("text"):
                st.caption(reference["text"][:500])


def get_settings() -> tuple[str, str, str]:
    csv_path = os.getenv("BAILIAN_CREDENTIAL_CSV", "")
    credentials = load_exported_credentials(csv_path) if csv_path else {}
    return (
        credentials.get("apiKey") or os.getenv("DASHSCOPE_API_KEY", ""),
        os.getenv("BAILIAN_AGENT_ID", ""),
        credentials.get("apiHost") or os.getenv("BAILIAN_API_HOST", ""),
    )


api_key, agent_id, api_host = get_settings()
if not api_key or not agent_id or not api_host:
    st.error("请配置百炼凭据 CSV、BAILIAN_AGENT_ID 和 API Host。")
    st.stop()


@st.cache_resource
def get_client() -> BailianClient:
    return BailianClient(api_key, agent_id, api_host)


if "messages" not in st.session_state:
    st.session_state.messages = []

if st.sidebar.button("清空对话", width="stretch"):
    st.session_state.messages = []
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
            history = [
                {"role": message["role"], "content": message["content"]}
                for message in st.session_state.messages
            ]
            with st.spinner("正在查询知识库..."):
                result = get_client().chat(history)
            st.markdown(result["text"])
            show_references(result["references"])
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
