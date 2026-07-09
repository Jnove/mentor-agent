"""LLM 客户端与所有提示词逻辑：回答生成、多轮问题改写、笔记要点压缩。"""
import os
import re

from openai import OpenAI

from core.config import llm_model

SYSTEM_PROMPT = """你是"学长组 Agent"，帮同学解答学校政策、通知、常见问题。
规则：
1. 只依据【资料】和【知识库目录】作答，里面没有的就说"知识库暂时没有相关信息，建议咨询XX部门"，绝不编造。
2. 引用标注：正文中每用到一份来源的信息，就在相应句子末尾标注它的编号，如 [3]；\
枚举知识库里有哪些文档时，同样在每个文档名后标注编号。\
除编号外不要罗列来源标题或链接——系统会根据你标注的编号自动在回答下方生成来源清单。
3. 政策有时效性，如果资料日期较旧，提醒同学以官网最新版为准。
4. 用简洁、友好的学长语气回答。
5. 【知识库目录】是知识库全部文档的清单。回答"有哪些/哪几种/多少个"这类枚举问题时，\
以目录数全，不要只数【资料】里出现的几篇；目录里有但【资料】没给正文的文档，只报标题加编号，不要编细节。"""


def get_llm() -> OpenAI:
    return OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_context(question: str, hits: list[dict],
                  catalog: list[dict] = ()) -> tuple[str, list[dict]]:
    """组装带来源编号的 prompt。

    返回 (prompt, sources)：sources[i] 对应正文引用编号 i+1。
    检索片段和目录里的同一篇文档共用一个编号（按 title+source_url 去重）。
    """
    sources: list[dict] = []
    index: dict[tuple, int] = {}

    def cite(m: dict) -> int:
        key = (m.get("title"), m.get("source_url"))
        if key not in index:
            index[key] = len(sources) + 1
            sources.append({k: str(m.get(k, "")) for k in (
                "title", "source_url", "source_org", "publish_date")})
        return index[key]

    blocks = [
        f"[{cite(h)}]《{h['title']}》({h['source_org']}, {h['publish_date']})\n{h['text']}"
        for h in hits
    ]

    cat_lines, prev_folder = [], None
    for m in catalog:
        f = str(m.get("file", ""))
        folder = f.rsplit("/", 1)[0] if "/" in f else "根目录"
        if folder != prev_folder:
            cat_lines.append(f"{folder}/")
            prev_folder = folder
        cat_lines.append(f"  [{cite(m)}]《{m.get('title')}》({m.get('publish_date')})")

    prompt = f"【知识库目录】\n" + "\n".join(cat_lines) + "\n\n" if cat_lines else ""
    prompt += "【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】\n{question}"
    return prompt, sources


_CITE = re.compile(r"\[(\d{1,2})\]")


def extract_citations(answer: str, sources: list[dict]) -> list[tuple[int, dict]]:
    """按正文首次引用顺序返回 (编号, 来源)；没有合法引用标记时返回空列表（调用方兜底）。"""
    out, seen = [], set()
    for m in _CITE.finditer(answer):
        n = int(m.group(1))
        if 1 <= n <= len(sources) and n not in seen:
            seen.add(n)
            out.append((n, sources[n - 1]))
    return out


def stream_answer(llm, history: list[dict], prompt: str):
    """流式生成回答。history 为不含当前问题的既往消息（只取最近几轮控制 token）。"""
    return llm.chat.completions.create(
        model=llm_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *history[-5:],
            {"role": "user", "content": prompt},
        ],
        stream=True,
    )


def rewrite_query(llm, history: list[dict], question: str) -> str:
    """把多轮追问改写成独立完整的检索问题。

    没有历史时原样返回；改写失败也原样返回（检索继续用原问题，不阻塞主流程）。
    """
    if not history:
        return question
    context = "\n".join(
        f"{'同学' if m['role'] == 'user' else '学长'}：{m['content'][:200]}"
        for m in history[-4:]
    )
    try:
        res = llm.chat.completions.create(
            model=llm_model(),
            messages=[{
                "role": "user",
                "content": (
                    "根据对话历史，把同学的最新问题改写成一个不依赖上下文、"
                    "可独立理解的完整问题，用于检索学校政策文档。"
                    "只输出改写后的问题本身，不要任何解释。"
                    "如果最新问题本身已经完整独立，原样输出即可。\n\n"
                    f"【对话历史】\n{context}\n\n【最新问题】\n{question}"
                ),
            }],
            stream=False,
        )
        rewritten = (res.choices[0].message.content or "").strip().strip("「」\"'")
        # 明显异常的输出（空/过长）不采用
        if rewritten and len(rewritten) <= max(60, len(question) * 4):
            return rewritten
    except Exception:
        pass
    return question


def summarize_turn(llm, question: str, answer: str) -> list[str]:
    """把一轮问答压缩成 2-3 条要点；LLM 失败时降级为截取答案前两句。"""
    answer = _CITE.sub("", answer)  # 引用编号对笔记要点是噪音
    try:
        res = llm.chat.completions.create(
            model=llm_model(),
            messages=[{
                "role": "user",
                "content": (
                    "把下面这轮问答压缩成 2-3 条要点，每条不超过 30 字。"
                    "只输出要点本身，每条一行，以「- 」开头，不要其他内容。\n\n"
                    f"问题：{question}\n\n回答：{answer}"
                ),
            }],
            stream=False,
        )
        lines = (res.choices[0].message.content or "").splitlines()
        points = [ln.strip().lstrip("-•・ ").strip() for ln in lines]
        points = [p for p in points if p]
        if points:
            return points[:3]
    except Exception:
        pass
    sents = re.split(r"(?<=[。！？!?])", answer)
    return [s.strip()[:40] for s in sents[:2] if s.strip()] or [answer[:40]]
