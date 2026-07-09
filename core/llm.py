"""LLM 客户端与所有提示词逻辑：回答生成、多轮问题改写、笔记要点压缩。"""
import os
import re

from openai import OpenAI

from core.config import llm_model

SYSTEM_PROMPT = """你是"学长组 Agent"，帮同学解答学校政策、通知、常见问题。
规则：
1. 只依据【资料】作答，资料里没有的就说"知识库暂时没有相关信息，建议咨询XX部门"，绝不编造。
2. 回答末尾必须列出用到的来源：标题 + 链接 + 发布日期；同一篇文档只列一次，不要重复。
3. 政策有时效性，如果资料日期较旧，提醒同学以官网最新版为准。
4. 用简洁、友好的学长语气回答。
5. 【知识库目录】是知识库全部文档的清单。回答"有哪些/哪几种/多少个"这类枚举问题时，\
以目录数全，不要只数【资料】里出现的几篇；目录里有但【资料】没给正文的文档，只报标题，不要编细节。"""


def get_llm() -> OpenAI:
    return OpenAI(
        api_key=os.environ["LLM_API_KEY"],
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_prompt(question: str, hits: list[dict], catalog: str = "") -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        blocks.append(
            f"[资料{i}]《{h['title']}》({h['source_org']}, {h['publish_date']})\n"
            f"链接: {h['source_url']}\n{h['text']}"
        )
    cat = f"【知识库目录】\n{catalog}\n\n" if catalog else ""
    return cat + "【资料】\n" + "\n\n".join(blocks) + f"\n\n【问题】\n{question}"


def stream_answer(llm, history: list[dict], question: str, hits: list[dict],
                  catalog: str = ""):
    """流式生成回答。history 为不含当前问题的既往消息（只取最近几轮控制 token）。"""
    return llm.chat.completions.create(
        model=llm_model(),
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *history[-5:],
            {"role": "user", "content": build_prompt(question, hits, catalog)},
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
