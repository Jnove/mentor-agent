"""Minimal Streamlit UI backed by Alibaba Cloud Model Studio Knowledge Chat."""

import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

if __package__:
    from .client import BailianClient, BailianError, load_exported_credentials
else:
    from client import BailianClient, BailianError, load_exported_credentials


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(Path(__file__).with_name(".env"))
load_dotenv(PROJECT_ROOT / "knowledge_base" / "mentor-agent.env")


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
    if csv_path and not Path(csv_path).is_absolute():
        csv_path = str(PROJECT_ROOT / csv_path)
    credentials = load_exported_credentials(csv_path) if csv_path else {}
    return (
        credentials.get("apiKey") or os.getenv("DASHSCOPE_API_KEY", ""),
        os.getenv("BAILIAN_AGENT_ID", ""),
        credentials.get("apiHost") or os.getenv("BAILIAN_API_HOST", ""),
    )


@st.cache_resource
def get_client(api_key: str, agent_id: str, api_host: str) -> BailianClient:
    return BailianClient(api_key, agent_id, api_host)


def render_chat() -> None:
    """Render the Bailian-backed policy chat page."""
    st.title("校园政策问答")
    st.caption("懂校园政策的专属学长")

    try:
        api_key, agent_id, api_host = get_settings()
    except BailianError as exc:
        st.error(str(exc))
        return
    if not api_key or not agent_id or not api_host:
        st.error("请配置知识库服务凭据、应用 ID 和 API Host。")
        return

    if "messages" not in st.session_state:
        st.session_state.messages = []

    if st.sidebar.button("清空对话", width="stretch"):
        st.session_state.messages = []
        st.rerun()

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])
            show_references(message.get("references", []))

    if not (question := st.chat_input("请输入学校政策问题")):
        return

    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    with st.chat_message("assistant", avatar="🎓"):
        try:
            history = [
                {"role": message["role"], "content": message["content"]}
                for message in st.session_state.messages
            ]
            result = get_client(api_key, agent_id, api_host).chat(history)
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


if __name__ == "__main__":
    st.set_page_config(page_title="校园政策问答", page_icon="🎓")
    render_chat()
