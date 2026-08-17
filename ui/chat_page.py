"""
学长组 Agent — Streamlit 问答界面（纯 UI 层，业务逻辑在 core/）

左侧「学长笔记」自动沉淀每轮问答要点，可一键导出 FAQ；右侧为检索问答。

配置：复制 .env.example 为 .env 并填入 API Key（OpenAI 兼容接口，DeepSeek/Qwen/Kimi 均可）

用法:
    streamlit run app.py
"""
import html
import logging
import os
import threading
from datetime import date

import streamlit as st

# Streamlit 的文件监视器会遍历 sys.modules 探测 __path__，探到 transformers 5.x 时触发它懒加载
# 视觉模型，而那些模块 import torchvision（本项目未装）→ ModuleNotFoundError。该异常被 Streamlit
# 内部 catch，不影响运行，只是把 traceback 刷进控制台。屏蔽这一条 warning 即可。
logging.getLogger("streamlit.watcher.local_sources_watcher").setLevel(logging.ERROR)

from core.config import BST_FALLBACK, BST_FALLBACK_SCORE, BST_TOP_N, COLLECTION, DB_DIR
from core import bst, teachers, usage
from core.embeddings import get_embedder
from core.llm import (
    build_context, get_llm, renumber_citations, rewrite_query, stream_answer,
    strip_citations, summarize_turn,
)
from core.notes import dedup_sources, notes_to_markdown, snippet
from core.retrieval import Retriever, load_reranker
from core.slang import expand_query


_resources = None
_resources_lock = threading.Lock()


def _build_resources():
    """并行加载模型 + 建检索索引（三者相互独立），缩短冷加载。"""
    from concurrent.futures import ThreadPoolExecutor

    import chromadb  # 导入较重，推迟到预热/首问时加载，首屏脚本只留轻量依赖

    col = chromadb.PersistentClient(path=DB_DIR).get_collection(COLLECTION)
    with ThreadPoolExecutor(max_workers=3) as ex:
        prepared_fut = ex.submit(Retriever._prepare, col)
        embed_fut = ex.submit(get_embedder)
        rerank_fut = ex.submit(load_reranker)
        prepared = prepared_fut.result()
        embed = embed_fut.result()
        reranker = rerank_fut.result()
    retriever = Retriever(embed, col, reranker=reranker, prepared=prepared)
    return retriever, get_llm()


def _ensure_resources():
    """线程安全懒加载单例；后台预热线程与首次提问共用同一份，跨 rerun 持久。"""
    global _resources
    if _resources is None:
        with _resources_lock:
            if _resources is None:
                _resources = _build_resources()
    return _resources


_prewarm_started = False


def _maybe_prewarm() -> None:
    """服务器启动后在后台预热检索资源；模块全局保证进程内只执行一次。

    防重标志放在本模块而非 app.py：chat_page 只 import 一次，标志跨 Streamlit
    rerun 持久；app.py 是主脚本，每轮 rerun 都会重跑，标志会被重新赋值。
    """
    global _prewarm_started
    if _prewarm_started:
        return
    _prewarm_started = True
    if not os.environ.get("LLM_API_KEY"):
        return  # 未配置时首问会走 st.error，不需要预热

    def worker():
        import time

        # 避开首屏渲染窗口：预热加载模型/建 BM25 会抢 import 锁和 CPU，
        # 先让页面正常渲染，几秒后再开始后台预热
        time.sleep(4)
        try:
            _ensure_resources()
        except Exception:
            logging.exception("prewarm 失败，首问时再加载")
        else:
            print("[prewarm] 检索资源已预热完成", flush=True)

    threading.Thread(target=worker, daemon=True).start()


def load_resources():
    import os

    if not os.environ.get("LLM_API_KEY"):
        st.error("未找到 LLM_API_KEY：请复制 .env.example 为 .env 并填入 API Key，然后重启。")
        st.stop()
    return _ensure_resources()


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


def _render_course_choose(i: int, msg: dict, card: dict) -> None:
    """数分这类一对多黑话：横向一排胶囊选项（st.pills），用户点哪门就出哪门的卡。

    点选后把「点选的这门课」折叠成一条 user 消息 + 该门课的 course 卡片追加进
    历史（选了一个 + 出一张卡，一问一答保持配平）；原消息标记 resolved，
    此后重跑不再重复渲染这些选项。
    """
    courses = card.get("courses") or []
    if not courses:
        return
    key = f"course_choose_{i}"
    if msg.get("resolved"):
        return  # 已确认，本消息只保留"请选择哪一门"的提示文本
    if msg.get("_chosen"):
        chosen = msg.pop("_chosen", None)
        msg["resolved"] = True
        st.session_state.messages.append({"role": "user", "content": chosen})
        result = teachers.course_card(chosen)
        st.session_state.messages.append({
            "role": "assistant",
            "content": teachers.render_card_html(result),
            "sources_md": "评教社区历史评分/评论，仅供参考",
            "retrieval": None, "card": result,
            "log_id": usage.log_question(
                st.session_state.user["id"], chosen, 0, None, False, False,
                kind="teacher"),
        })
        st.rerun()
    picked = st.pills(
        "这个叫法可能对应多门课，请选择你具体要问哪一门：",
        courses, key=f"{key}_pills", label_visibility="visible",
    )
    if picked:
        msg["_chosen"] = picked
        st.rerun()


def render_chat():
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "notes" not in st.session_state:
        st.session_state.notes = []

    st.markdown(
        '<div class="eyebrow">Mentor Group · 学长知识台</div>'
        '<div class="brand">学长组<span class="apo">\'s</span> Agent</div>'
        '<div class="brand-sub">校园政策问答 · 回答均附来源 · 本地知识库</div>'
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
            for i, msg in enumerate(st.session_state.messages):
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
                    # 老师卡片（card 字段）用结构化重绘，普通消息用正文；
                    # content 里存的也是 HTML，无 unsafe_allow_html 会变裸标签
                    if msg.get("card"):
                        card = msg["card"]
                        st.markdown(teachers.render_card_html(card), unsafe_allow_html=True)
                        # 数分这类一对多黑话：出「请确认具体哪门课」的选择器，
                        # 确认后折叠成选课卡片，本消息不再重复渲染选择器
                        if card.get("kind") == "course_choose":
                            _render_course_choose(i, msg, card)
                    else:
                        st.markdown(msg["content"])
                    if msg.get("sources_md"):
                        st.caption("来源：" + msg["sources_md"])
                    if msg["role"] == "assistant" and msg.get("log_id"):
                        log_id = msg["log_id"]
                        if msg.get("feedback") is None:
                            c_up, c_down = st.columns(2)
                            with c_up:
                                if st.button("帮上了", key=f"fb_{log_id}_up", type="tertiary", use_container_width=True):
                                    usage.set_feedback(log_id, usage.FEEDBACK_UP)
                                    st.session_state.messages[i]["feedback"] = usage.FEEDBACK_UP
                                    st.rerun()
                            with c_down:
                                if st.button("没帮上", key=f"fb_{log_id}_down", type="tertiary", use_container_width=True):
                                    usage.set_feedback(log_id, usage.FEEDBACK_DOWN)
                                    st.session_state.messages[i]["feedback"] = usage.FEEDBACK_DOWN
                                    st.rerun()
                        else:
                            fb = msg.get("feedback")
                            st.caption("已反馈：" + ("帮上了" if fb == usage.FEEDBACK_UP else "没帮上"))
        question = st.chat_input("例如：数院转专业需要什么条件？/数院的刘康生老师风评如何？")

    if question:
        # 不含当前问题；只保留 role/content，附加字段（sources_md）不能发给 LLM 接口。
        # assistant 历史要洗掉引用编号——那些编号对应上一轮的来源表，喂回去会被
        # 模型照抄、在本轮重编号时串到错误来源
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
                tcard = None
                try:
                    # 查老师短路：识别到「评价某老师」提问时，不加载 RAG 资源（embedding/
                    # reranker/BM25 是重活，老师卡片用不到），直接出结构化卡片。
                    # get_llm() 只建 OpenAI 客户端（轻量），供速评/重名消歧/抽取兜底。
                    llm = get_llm() if os.environ.get("LLM_API_KEY") else None
                    tcard = teachers.maybe_card(question, question, llm=llm)
                    if tcard is not None:
                        card_html = teachers.render_card_html(tcard)
                        st.markdown(card_html, unsafe_allow_html=True)
                        # 老师卡片自成链路（kind='teacher'）不参与「未覆盖问题」统计
                        card_log = usage.log_question(
                            st.session_state.user["id"], question, 0, None,
                            False, False, kind="teacher",
                        )
                        st.session_state.messages.append({
                            "role": "assistant", "content": card_html,
                            "sources_md": "评教社区历史评分/评论，仅供参考",
                            "retrieval": None, "card": tcard, "log_id": card_log,
                        })
                        # 首问的 course_choose 要当场出选项：消息循环在本次脚本顶部已经
                        # 跑过（当时 messages 还是空的，轮不到这一条），只有靠 rerun 才会
                        # 走到 _render_course_choose。这里补一次，跟后续 rerun 用同一 key。
                        if tcard.get("kind") == "course_choose":
                            _render_course_choose(
                                len(st.session_state.messages) - 1,
                                st.session_state.messages[-1], tcard)
                        st.session_state.last_hit_ids = ()
                        ok = True
                    else:
                        with st.spinner("检索知识库中..."):
                            # 嵌入模型、Chroma、BM25 与重排器初始化较慢，首屏不需要；
                            # 推迟到首次真正提问并由 cache_resource 保证后续复用。
                            retriever, llm = load_resources()
                            search_q = rewrite_query(llm, history, question)
                            # 追问时把上一轮命中并入重排候选池，答案前后依据保持连贯
                            hits = retriever.search(
                                search_q,
                                carry_ids=st.session_state.get("last_hit_ids", ()),
                            )
                            # 百事通实时兜底：知识库无命中或最高分过低（库外问题）时，
                            # 实时检索全校常见问题 + 网页资讯补进 prompt。结果结构与
                            # RAG hits 兼容（title/source_url/… 齐全），build_context 原样处理
                            top = max((h.get("score") or 0) for h in hits) if hits else 0
                            fallback = False
                            if BST_FALLBACK and (not hits or top < BST_FALLBACK_SCORE):
                                bst_hits = bst.bst_search(search_q, top_n=BST_TOP_N)
                                if bst_hits:
                                    hits = [*hits, *bst_hits]
                                    fallback = True
                        # 检索详情随消息保存，重跑后历史循环里能原样重绘。
                        # 展示的是真实检索词：含改写和黑话扩展（search 内部同一函数），
                        # 否则扩展词把意外文档拉进结果时从 UI 上查不出原因
                        shown_q = expand_query(search_q)
                        retrieval = {
                            "n": len(hits),
                            "bst": fallback,
                            "bst_n": len(bst_hits) if fallback else 0,
                            "top_score": top,
                            "rewritten": shown_q if shown_q != question else "",
                            "items": [
                                (
                                    f"- 《{h['title']}》〔百事通 · {h['from_bst']}〕— {snippet(h['text'])}"
                                    if h.get("from_bst") else f"- 《{h['title']}》— {snippet(h['text'])}"
                                )
                                for h in hits
                            ],
                        }
                        if fallback:
                            exp_title = (
                                f"知识库未命中，百事通实时补充 {retrieval['bst_n']} 条"
                                if not hits or all(h.get("from_bst") for h in hits)
                                else f"知识库命中 {retrieval['n'] - retrieval['bst_n']} 条 + 百事通补充 {retrieval['bst_n']} 条"
                            )
                        else:
                            exp_title = (
                                f"检索到 {retrieval['n']} 条相关片段"
                                if retrieval["n"] else "未检索到相关片段"
                            )
                        with st.expander(exp_title):
                            if retrieval["rewritten"]:
                                st.caption(f"实际检索词：{retrieval['rewritten']}")
                            for line in retrieval["items"]:
                                st.markdown(line)

                        prompt, cite_srcs = build_context(question, hits, retriever.catalog)
                        answer_slot = st.empty()  # 占位：流式结束后用重编号正文原地替换
                        streamed = answer_slot.write_stream(stream_answer(llm, history, prompt))
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
                        if caption:  # 零命中且模型未标注引用时没有来源，不渲染裸标签
                            st.caption("来源：" + caption)
                        ok = True
                except Exception as e:
                    # Streamlit 自身的控制流异常必须放行（当前版本继承 BaseException
                    # 不会进这里，但旧版继承 Exception，防一手）
                    if type(e).__module__.startswith("streamlit"):
                        raise
                    # 网关前置 Cloudflare、403 有前科（见 core/llm.py），主链路必须兜底：
                    # 学生不能看到 traceback
                    logging.exception("回答生成失败")
                    if answer_slot is not None:
                        answer_slot.empty()  # 清掉半截流式输出
                    st.error("学长这会儿开小差了（网络或模型服务波动），请稍等片刻再问一次。")
                finally:
                    # 失败或被打断（流式中途用户提交新问题会触发 Streamlit 的
                    # BaseException 级中断）都回滚本轮 user 消息，历史保持一问一答
                    # 配平，否则下一轮 history 出现连续两条 user
                    if not ok:
                        st.session_state.messages.pop()

        if ok and tcard is None:
            if hits:
                # 零命中轮（库外问题被阈值过滤光）不覆写：中间插一个闲聊问题
                # 不应该把上一轮的追问连续性状态清掉
                st.session_state.last_hit_ids = [h["id"] for h in hits if "id" in h]
            kb_hits = sum(1 for h in hits if not h.get("from_bst"))
            log_id = usage.log_question(
                st.session_state.user["id"], question, kb_hits, top, fallback,
                kb_hits > 0,
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": answer,
                 "sources_md": caption, "retrieval": retrieval, "log_id": log_id}
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
