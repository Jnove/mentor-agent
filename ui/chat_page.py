"""
学长组 Agent — Streamlit 问答界面（纯 UI 层，业务逻辑在 core/）

左侧「学长笔记」自动沉淀每轮问答要点，可一键导出 FAQ；右侧为检索问答。

配置：复制 .env.example 为 .env 并填入 API Key（OpenAI 兼容接口，DeepSeek/Qwen/Kimi 均可）

用法:
    streamlit run app.py
"""
import html
import logging
from datetime import date

import chromadb
import streamlit as st

# Streamlit 的文件监视器会遍历 sys.modules 探测 __path__，探到 transformers 5.x 时触发它懒加载
# 视觉模型，而那些模块 import torchvision（本项目未装）→ ModuleNotFoundError。该异常被 Streamlit
# 内部 catch，不影响运行，只是把 traceback 刷进控制台。屏蔽这一条 warning 即可。
logging.getLogger("streamlit.watcher.local_sources_watcher").setLevel(logging.ERROR)

from core.config import COLLECTION, DB_DIR
from core.embeddings import get_embedder
from core.llm import (
    build_context, get_llm, renumber_citations, rewrite_query, stream_answer,
    summarize_turn,
)
from core.notes import dedup_sources, notes_to_markdown, snippet
from core.retrieval import Retriever, load_reranker


@st.cache_resource
def load_resources():
    import os

    if not os.environ.get("LLM_API_KEY"):
        st.error("未找到 LLM_API_KEY：请复制 .env.example 为 .env 并填入 API Key，然后重启。")
        st.stop()
    embed = get_embedder()
    col = chromadb.PersistentClient(path=DB_DIR).get_collection(COLLECTION)
    retriever = Retriever(embed, col, reranker=load_reranker())
    return retriever, get_llm()


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
    retriever, llm = load_resources()

    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "notes" not in st.session_state:
        st.session_state.notes = []

    st.markdown(
        '<div class="eyebrow">Mentor Group · 学长知识台</div>'
        '<div class="brand">学长组<span class="apo">\'s</span> Agent</div>'
        f'<div class="brand-sub">校园政策问答 · 回答均附来源 · 知识库 {retriever.col.count()} 个片段</div>'
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
                        with st.expander(f"检索到 {r['n']} 条相关片段"):
                            if r["rewritten"]:
                                st.caption(f"追问已改写为：{r['rewritten']}")
                            for line in r["items"]:
                                st.markdown(line)
                    st.markdown(msg["content"])
                    if msg.get("sources_md"):
                        st.caption("来源：" + msg["sources_md"])
        question = st.chat_input("例如：转专业需要什么条件？")

    if question:
        # 不含当前问题；只保留 role/content，附加字段（sources_md）不能发给 LLM 接口
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state.messages
        ]
        st.session_state.messages.append({"role": "user", "content": question})
        with chat_box:
            with st.chat_message("user"):
                st.markdown(question)

            with st.chat_message("assistant", avatar="🎓"):
                with st.spinner("检索知识库中..."):
                    search_q = rewrite_query(llm, history, question)
                    hits = retriever.search(search_q)
                # 检索详情随消息保存，重跑后历史循环里能原样重绘
                retrieval = {
                    "n": len(hits),
                    "rewritten": search_q if search_q != question else "",
                    "items": [f"- 《{h['title']}》— {snippet(h['text'])}" for h in hits],
                }
                with st.expander(f"检索到 {retrieval['n']} 条相关片段"):
                    if retrieval["rewritten"]:
                        st.caption(f"追问已改写为：{retrieval['rewritten']}")
                    for line in retrieval["items"]:
                        st.markdown(line)

                prompt, cite_srcs = build_context(question, hits, retriever.catalog)
                answer_slot = st.empty()  # 占位：流式结束后用重编号正文原地替换
                streamed = answer_slot.write_stream(
                    (chunk.choices[0].delta.content or "")
                    for chunk in stream_answer(llm, history, prompt)
                    if chunk.choices
                )
                # write_stream 返回 str | list；全是字符串块时归一成 str
                answer = streamed if isinstance(streamed, str) else "".join(map(str, streamed))

                # 引用重映射为按出现顺序的 [1][2][3]…；来源清单跟着正文引用走，
                # LLM 没标注时退回"检索命中去重"
                answer, cited = renumber_citations(answer, cite_srcs)
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
                st.caption("来源：" + caption)

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
