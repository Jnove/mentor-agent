"""
学长组 Agent — Streamlit 问答界面（纯 UI 层，业务逻辑在 core/）

左侧「学长笔记」自动沉淀每轮问答要点，可一键导出 FAQ；右侧为检索问答。

回答由阿里云百炼知识库生成；左侧笔记仍使用原有 OpenAI 兼容模型总结。

用法:
    streamlit run app.py
"""
import html
import logging
from datetime import date
from pathlib import Path

import streamlit as st

# Streamlit 的文件监视器会遍历 sys.modules 探测 __path__，探到 transformers 5.x 时触发它懒加载
# 视觉模型，而那些模块 import torchvision（本项目未装）→ ModuleNotFoundError。该异常被 Streamlit
# 内部 catch，不影响运行，只是把 traceback 刷进控制台。屏蔽这一条 warning 即可。
logging.getLogger("streamlit.watcher.local_sources_watcher").setLevel(logging.ERROR)

from bailian_agent.app import get_client, get_settings
from bailian_agent.client import BailianError, renumber_references
from bailian_agent.local_sources import attach_original_urls
from core.llm import (
    get_llm, strip_citations, summarize_turn,
)
from core.notes import dedup_sources, notes_to_markdown, snippet


ZW_DIR = Path(__file__).resolve().parents[1] / "knowledge_base" / "zw"


@st.cache_resource
def load_resources():
    import os

    if not os.environ.get("LLM_API_KEY"):
        st.error("未找到 LLM_API_KEY：左侧学长笔记需要原有模型配置。")
        st.stop()
    try:
        api_key, agent_id, api_host = get_settings()
    except BailianError as exc:
        st.error(str(exc))
        st.stop()
    if not api_key or not agent_id or not api_host:
        st.error("请配置百炼凭据 CSV、BAILIAN_AGENT_ID 和 API Host。")
        st.stop()
    return get_client(api_key, agent_id, api_host), get_llm()


def note_card_html(n: dict) -> str:
    q = html.escape(n["q"])
    points = "".join(f"<li>{html.escape(p)}</li>" for p in n["points"])
    srcs = "".join(
        f'<div class="note-src-item">'
        f'<a href="{html.escape(s["source_url"])}" target="_blank">《{html.escape(s["title"])}》</a>'
        f'</div>'
        for s in n["sources"]
    )
    src_line = f'<div class="note-src">{srcs}</div>' if srcs else ""
    return (
        f'<div class="note-card"><div class="note-q">{q}</div>'
        f'<ul class="note-points">{points}</ul>{src_line}</div>'
    )


def render_chat():
    bailian, llm = load_resources()

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "notes" not in st.session_state:
        st.session_state.notes = []

    st.markdown(
        '<div class="eyebrow">Mentor Group · 学长知识台</div>'
        '<div class="brand">学长组<span class="apo">\'s</span> Agent</div>'
        '<div class="brand-sub">校园政策问答 · 回答均附来源 · 阿里云百炼知识库</div>'
        '<div class="brand-rule"></div>',
        unsafe_allow_html=True,
    )

    left, right = st.columns([2, 3], gap="large")

    # —— 右栏：问答（先执行，让本轮新笔记能出现在左栏）——
    with right:
        st.markdown(
            '<div class="panel-title"><span class="bar bar-teal"></span>问答</div>',
            unsafe_allow_html=True,
        )
        chat_box = st.container(height=470, key="chat_box")
        with chat_box:
            for msg in st.session_state.messages:
                avatar = "🎓" if msg["role"] == "assistant" else None
                with st.chat_message(msg["role"], avatar=avatar):
                    # 检索详情和来源行都存在消息里，重跑（导出/追问）后一起重绘
                    r = msg.get("retrieval")
                    if r:
                        title = f"检索到 {r['n']} 条相关片段" if r["n"] else "未检索到相关片段"
                        with st.expander(title):
                            if r["rewritten"]:
                                st.caption(f"实际检索词：{r['rewritten']}")
                            for line in r["items"]:
                                st.markdown(line)
                    st.markdown(msg["content"])
                    if msg.get("sources_md"):
                        st.caption("来源：" + msg["sources_md"])
        question = st.chat_input("例如：转专业需要什么条件？")

    if question:
        # 百炼只接收 role/content；上一轮的本地引用编号不能带入下一轮。
        history = [
            {"role": m["role"],
             "content": strip_citations(m["content"])
             if m["role"] == "assistant" else m["content"]}
            for m in st.session_state.messages
        ]
        st.session_state.messages.append({"role": "user", "content": question})
        ok = False
        with chat_box:
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant", avatar="🎓"):
                answer_slot = None
                try:
                    with st.spinner("查询百炼知识库中..."):
                        result = bailian.chat([
                            *history,
                            {"role": "user", "content": question},
                        ])

                    references = attach_original_urls(result["references"], ZW_DIR)
                    hits = [
                        {
                            "id": ref.get("doc_id") or str(ref["index"]),
                            "citation_index": ref["index"],
                            "title": ref["title"],
                            "text": ref.get("text", ""),
                            "source_url": ref.get("source_url", ""),
                            "source_org": "阿里云百炼知识库",
                            "publish_date": "",
                        }
                        for ref in references
                    ]
                    retrieval = {
                        "n": len(hits),
                        "rewritten": "",
                        "items": [
                            f"- 《{h['title']}》— {snippet(h['text'])}"
                            for h in hits
                        ],
                    }
                    exp_title = (
                        f"百炼检索到 {retrieval['n']} 条参考资料"
                        if retrieval["n"] else "百炼未返回参考资料"
                    )
                    with st.expander(exp_title):
                        for line in retrieval["items"]:
                            st.markdown(line)

                    answer_slot = st.empty()
                    answer = result["text"]
                    answer, cited = renumber_references(answer, hits)
                    answer_slot.markdown(answer)
                    if cited:
                        sources = [s for _, s in cited]
                        caption = " · ".join(
                            f"[{n}] [《{s['title']}》]({s['source_url']})" for n, s in cited
                        )
                    else:
                        sources = dedup_sources(hits)
                        caption = " · ".join(
                            f"[《{s['title']}》]({s['source_url']})" for s in sources
                        )
                    if caption:  # 零命中且模型未标注引用时没有来源，不渲染裸标签
                        st.caption("来源：" + caption)
                    ok = True
                except Exception as e:
                    if type(e).__module__.startswith("streamlit"):
                        raise
                    logging.exception("回答生成失败")
                    if answer_slot is not None:
                        answer_slot.empty()
                    st.error("学长这会儿开小差了（网络或模型服务波动），请稍等片刻再问一次。")
                finally:
                    if not ok:
                        st.session_state.messages.pop()

        if ok:
            st.session_state.messages.append(
                {"role": "assistant", "content": answer,
                 "sources_md": caption, "retrieval": retrieval}
            )
            with st.spinner("整理笔记中..."):
                points = summarize_turn(llm, question, answer)
            st.session_state.notes.append(
                {"q": question, "points": points, "sources": sources}
            )

    # —— 左栏：学长笔记（后执行，包含本轮新笔记）——
    with left:
        n_notes = len(st.session_state.notes)
        count_pill = f'<span class="count-pill">{n_notes}</span>' if n_notes else ""
        st.markdown(
            f'<div class="panel-title"><span class="bar bar-bronze"></span>学长笔记 {count_pill}</div>',
            unsafe_allow_html=True,
        )
        notes_box = st.container(height=470, key="notes_box")
        with notes_box:
            if not st.session_state.notes:
                st.markdown(
                    '<div class="note-empty"><div class="ghost">问</div>'
                    '<div class="hint">提问后，这里会自动沉淀每轮问答的要点</div></div>',
                    unsafe_allow_html=True,
                )
            else:
                for n in reversed(st.session_state.notes):
                    st.markdown(note_card_html(n), unsafe_allow_html=True)
        if st.session_state.notes:
            st.download_button(
                "导出 FAQ · Markdown",
                notes_to_markdown(st.session_state.notes),
                file_name=f"学长组FAQ_{date.today()}.md",
                mime="text/markdown",
                width="stretch",
            )
