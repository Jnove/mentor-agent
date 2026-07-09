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
from core.llm import get_llm, rewrite_query, stream_answer, summarize_turn
from core.notes import dedup_sources, notes_to_markdown, snippet
from core.retrieval import Retriever, load_reranker

# 设计语言：象牙白底 #FFFEF8，青 #0F7B72 × 青铜 #9A6B2F，毛玻璃卡片
# 签名元素：贯穿顶部的青→青铜渐变细线（呼应 jnove.dpdns.org）；
# 衬线品牌标题 + 青铜斜体 's；Vivia 式竖条小节标题；
# 毛玻璃配方参考 silhouette.dpdns.org：blur(18px) saturate(1.65) + 半透明白底 + 白描边 + 彩色柔影。
THEME_CSS = """
<style>
@import url('https://fonts.loli.net/css2?family=Noto+Serif+SC:wght@700;900&display=swap');

header[data-testid="stHeader"] { display: none; }
.stApp { background: #FFFEF8; }
/* 垫在毛玻璃后面的两团光晕：青（右上）+ 青铜（左下），没有它们玻璃糊不出层次 */
.stApp::before {
  content: ''; position: fixed; inset: 0; pointer-events: none;
  background:
    radial-gradient(ellipse 42% 38% at 82% 8%, rgba(15,123,114,.16), transparent 70%),
    radial-gradient(ellipse 40% 36% at 10% 88%, rgba(154,107,47,.14), transparent 70%),
    radial-gradient(ellipse 30% 26% at 45% 55%, rgba(15,123,114,.05), transparent 70%);
}
.block-container { padding: 1.6rem 2.4rem 0.8rem; max-width: 1240px; }

.top-thread {
  position: fixed; top: 0; left: 0; right: 0; height: 3px; z-index: 999999;
  background: linear-gradient(90deg, #0F7B72 0%, #6FAE9B 35%, #C9A46B 65%, #9A6B2F 100%);
}

.eyebrow {
  letter-spacing: .35em; color: #0F7B72; font-size: .72rem; font-weight: 700;
  text-transform: uppercase; margin-bottom: .15rem;
}
.brand {
  font-family: Georgia, 'Noto Serif SC', 'Source Han Serif SC', serif;
  font-size: 2.05rem; font-weight: 900; color: #22332F; line-height: 1.15;
}
.brand .apo { font-style: italic; color: #9A6B2F; font-weight: 700; }
.brand-sub { color: #8C948F; font-size: .88rem; margin-top: .2rem; }
.brand-rule {
  width: 72px; height: 3px; border-radius: 2px; margin: .7rem 0 1rem;
  background: linear-gradient(90deg, #0F7B72, #9A6B2F);
}

.panel-title {
  display: flex; align-items: center; gap: .55rem;
  font-size: 1.02rem; font-weight: 700; color: #22332F; margin: .1rem 0 .55rem;
}
.panel-title .bar { width: 4px; height: 1.05em; border-radius: 2px; }
.bar-teal { background: #0F7B72; }
.bar-bronze { background: #9A6B2F; }
.count-pill {
  background: rgba(154,107,47,.14); color: #7F5624; border-radius: 999px;
  font-size: .72rem; font-weight: 700; padding: .05rem .55rem;
}

/* 毛玻璃卡片（笔记面板 / 聊天面板）；st.container(key=...) 生成的稳定 class */
.st-key-chat_box, .st-key-notes_box {
  background: rgba(255,255,255,.62);
  backdrop-filter: blur(18px) saturate(1.65);
  -webkit-backdrop-filter: blur(18px) saturate(1.65);
  border: 1px solid rgba(255,255,255,.78); border-radius: 18px;
  box-shadow: rgba(15,123,114,.10) 0 24px 56px, rgba(30,41,36,.06) 0 3px 12px;
}

.note-card {
  background: rgba(255,255,254,.72); border: 1px solid rgba(255,255,255,.85);
  border-left: 3px solid #0F7B72;
  border-radius: 14px; padding: .75rem .95rem .65rem; margin-bottom: .7rem;
  box-shadow: rgba(30,41,36,.05) 0 2px 8px;
  transition: transform .18s ease, box-shadow .18s ease;
}
.note-card:hover { transform: translateY(-1px); box-shadow: rgba(154,107,47,.14) 0 6px 18px; }
.note-q { font-weight: 700; color: #22332F; margin-bottom: .35rem; }
.note-points { margin: 0 0 .45rem 1.1rem; padding: 0; color: #3E4A49; font-size: .9rem; }
.note-points li { margin-bottom: .15rem; }
.note-src { font-size: .78rem; color: #9AA39D; }
.note-src a { color: #8C6428; text-decoration: none; }
.note-src a:hover { text-decoration: underline; }

.note-empty { position: relative; text-align: center; padding: 2.4rem 1rem; }
.note-empty .ghost {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-family: Georgia, 'Noto Serif SC', serif; font-size: 6.5rem; font-weight: 900;
  color: rgba(154,107,47,.10); user-select: none; pointer-events: none;
}
.note-empty .hint { position: relative; z-index: 1; color: #9AA39D; font-size: .88rem; margin-top: 3.6rem; }

[data-testid="stChatMessage"] {
  background: rgba(255,255,254,.72); border: 1px solid rgba(255,255,255,.85);
  border-radius: 16px; padding: .6rem .8rem;
  box-shadow: rgba(30,41,36,.04) 0 2px 8px;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  background: rgba(15,123,114,.10); border-color: rgba(15,123,114,.12);
}

[data-testid="stChatInput"] {
  background: rgba(255,255,255,.70);
  backdrop-filter: blur(18px) saturate(1.65);
  -webkit-backdrop-filter: blur(18px) saturate(1.65);
  border: 1.5px solid rgba(255,255,255,.8); border-radius: 999px;
  box-shadow: rgba(30,41,36,.07) 0 2px 12px;
}
[data-testid="stChatInput"]:focus-within { border-color: #0F7B72; }
[data-testid="stChatInput"] > div { background: transparent; border: none; }

.stDownloadButton button {
  background: #9A6B2F !important; color: #fff !important; border: none !important;
  border-radius: 999px !important; font-weight: 700;
  box-shadow: rgba(154,107,47,.35) 0 2px 8px;
}
.stDownloadButton button:hover { background: #7F5624 !important; }

div[data-testid="stExpander"] details {
  background: rgba(255,255,254,.6); border: 1px solid rgba(154,107,47,.14); border-radius: 12px;
}

*::-webkit-scrollbar { width: 8px; height: 8px; }
*::-webkit-scrollbar-thumb { background: #E3DCC8; border-radius: 4px; }
*::-webkit-scrollbar-track { background: transparent; }
</style>
"""


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
    srcs = " · ".join(
        f'<a href="{html.escape(s["source_url"])}" target="_blank">《{html.escape(s["title"])}》</a>'
        for s in n["sources"]
    )
    src_line = f'<div class="note-src">{srcs}</div>' if srcs else ""
    return (
        f'<div class="note-card"><div class="note-q">{q}</div>'
        f'<ul class="note-points">{points}</ul>{src_line}</div>'
    )


st.set_page_config(page_title="学长组 Agent", page_icon="🎓", layout="wide")
st.markdown(THEME_CSS, unsafe_allow_html=True)

retriever, llm = load_resources()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "notes" not in st.session_state:
    st.session_state.notes = []

st.markdown(
    '<div class="top-thread"></div>'
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
                st.markdown(msg["content"])
    question = st.chat_input("例如：转专业需要什么条件？")

if question:
    history = list(st.session_state.messages)  # 不含当前问题
    st.session_state.messages.append({"role": "user", "content": question})
    with chat_box:
        with st.chat_message("user"):
            st.markdown(question)

        with st.chat_message("assistant", avatar="🎓"):
            with st.spinner("检索知识库中..."):
                search_q = rewrite_query(llm, history, question)
                hits = retriever.search(search_q)
            with st.expander(f"检索到 {len(hits)} 条相关片段"):
                if search_q != question:
                    st.caption(f"追问已改写为：{search_q}")
                for h in hits:
                    st.markdown(f"- 《{h['title']}》— {snippet(h['text'])}")

            streamed = st.write_stream(
                (chunk.choices[0].delta.content or "")
                for chunk in stream_answer(llm, history, question, hits, retriever.catalog)
                if chunk.choices
            )
            # write_stream 返回 str | list；全是字符串块时归一成 str
            answer = streamed if isinstance(streamed, str) else "".join(map(str, streamed))

            sources = dedup_sources(hits)
            st.caption(
                "来源："
                + " · ".join(
                    f"《{s['title']}》[{s['source_org']}]({s['source_url']})"
                    for s in sources
                )
            )

    st.session_state.messages.append({"role": "assistant", "content": answer})
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
            use_container_width=True,
        )
