"""校园黑话 → 正式名词的静态检索扩展。

新生首问最口语化，却不经过 rewrite_query（首问无历史直接返回），黑话的
BM25 词面对不上正式文档、小模型向量对校园专名也不可靠——在检索入口把
命中词条的正式说法追加到 query 尾部，两路召回和重排共用扩展后的 query。

词条存于统一黑话表 knowledge_base/slang.json（KB 子仓库跟踪、admin 页统一管理），
本模块只消费其中 type=rag 的条目（黑话 → 正式语词字符串）。
词条只收「文档里用正式名词、同学口中用黑话」的映射；正式名词本身已经
可检索的不收。攒到新的黑话 miss（看 tests/eval_retrieval.py 的 miss 清单）
就往 knowledge_base/slang.json 加一条。"""

from core.config import SLANG_FILE


def _load_slang() -> dict[str, str]:
    """加载 RAG 黑话（type=rag）；缺失/损坏 → {}。"""
    import json
    try:
        with open(SLANG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        out: dict[str, str] = {}
        for slang, entry in data.items():
            if isinstance(entry, dict) and entry.get("type") == "rag":
                value = entry.get("value")
                if isinstance(value, str) and value:
                    out[slang] = value
        return out
    except (OSError, ValueError):
        return {}


def _load_all() -> dict[str, dict]:
    """加载统一黑话表全部条目（type=rag / type=course）；缺失/损坏 → {}。

    供 admin 页统一展示/写回用，返回 {黑话: 原始条目 dict}，不区分类型。
    """
    import json
    try:
        with open(SLANG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (OSError, ValueError):
        return {}


def save_rag_slang(slang: str, value: str) -> str | None:
    """往统一黑话表写一条 type=rag 映射；校验失败返回错误，成功返回 None。

    value: 正式语词（字符串）。重复黑话覆盖旧值；只改 type=rag，不动课程黑话。
    """
    import json
    slang = (slang or "").strip()
    value = (value or "").strip()
    if not slang:
        return "黑话词不能为空"
    if not value:
        return "正式语词不能为空"
    data = _load_all()
    data[slang] = {"type": "rag", "value": value}
    try:
        with open(SLANG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return "写入失败: %s" % e
    return None


def delete_rag_slang(slang: str) -> str | None:
    """删除统一黑话表里一条 type=rag 映射；无此条按已删除处理。绝不动课程黑话。"""
    import json
    slang = (slang or "").strip()
    if not slang:
        return "黑话词不能为空"
    data = _load_all()
    entry = data.get(slang)
    if not (isinstance(entry, dict) and entry.get("type") == "rag"):
        return None
    data.pop(slang, None)
    try:
        with open(SLANG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError as e:
        return "写入失败: %s" % e
    return None


def expand_query(query: str) -> str:
    """命中词条时把正式说法追加到 query 尾部；未命中原样返回。"""
    extra = [
        formal for slang, formal in _load_slang().items()
        if slang in query and formal not in query
    ]
    return f"{query} {' '.join(extra)}" if extra else query
