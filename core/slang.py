"""校园黑话 → 正式名词的静态检索扩展。

新生首问最口语化，却不经过 rewrite_query（首问无历史直接返回），黑话的
BM25 词面对不上正式文档、小模型向量对校园专名也不可靠——在检索入口把
命中词条的正式说法追加到 query 尾部，两路召回和重排共用扩展后的 query。

词条存于统一黑话表 knowledge_base/slang.json（KB 子仓库跟踪、admin 页统一管理），
本模块只消费其中 type=rag 的条目（黑话 → 正式语词字符串）。
词条只收「文档里用正式名词、同学口中用黑话」的映射；正式名词本身已经
可检索的不收。攒到新的黑话 miss（看 tests/eval_retrieval.py 的 miss 清单）
就往 knowledge_base/slang.json 加一条。

读改写样板（read+validate / atomic write）由本模块的 read_slang_json() 与
atomic_write_slang_json() 统一提供；core/teachers.py 的 save/delete
course_slang 直接调用，避免三份 JSON 处理代码各漂移一份。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from core.config import SLANG_FILE


def read_slang_json(path=None) -> dict:
    """读取 slang JSON 全部条目；缺失/损坏/类型不对 → {}。

    path 缺省走默认 SLANG_FILE（=core.config.SLANG_FILE），传入 path 可由调用方
    指定临时文件——测试用 monkeypatch 时必须显式传，否则闭包了导入时的全局路径。
    按 (path, mtime) 缓存：管理员通过 atomic_write_slang_json 写入会触发 os.replace，
    新文件的 mtime 跟旧的不同，下一次读取自动重建；测试 monkeypatch 改路径也是新 key。
    """
    target = Path(path) if path else Path(SLANG_FILE)
    key = str(target)
    try:
        mtime = target.stat().st_mtime
    except OSError:
        mtime = 0.0
    cached = _SLANG_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        result = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        result = {}
    _SLANG_CACHE[key] = (mtime, result)
    return result


# 按 (path, mtime) 缓存，避免每次提问都重读 slang.json（admin 写入通过
# atomic_write_slang_json 走 os.replace，mtime 自动更新；测试 monkeypatch 改路径
# 也是新 key，缓存不串）。
_SLANG_CACHE: dict[str, tuple[float, dict]] = {}


def _load_slang() -> dict[str, str]:
    """加载 RAG 黑话（type=rag）；缺失/损坏 → {}。"""
    out: dict[str, str] = {}
    for slang, entry in read_slang_json().items():
        if isinstance(entry, dict) and entry.get("type") == "rag":
            value = entry.get("value")
            if isinstance(value, str) and value:
                out[slang] = value
    return out


def _load_all() -> dict[str, dict]:
    """加载统一黑话表全部条目（type=rag / type=course）；缺失/损坏 → {}。

    供 admin 页统一展示/写回用，返回 {黑话: 原始条目 dict}，不区分类型。
    """
    return read_slang_json()


def atomic_write_slang_json(data, path=None) -> str | None:
    """原子写 slang JSON：写临时文件后 os.replace 改名，避免崩在 dump 中途留下半截 JSON。
    写入前检测 path 是否落在 git 子模块里——如果是，提示管理员需要单独
    提交子模块，否则下次 update_kb / git submodule update --remote 会覆盖本地的修改。

    失败返回错误信息，成功返回 None。path 缺省走默认 SLANG_FILE。
    """
    slang_path = Path(path) if path else Path(SLANG_FILE)
    try:
        # 子模块检测：git submodule 目录里跑 git rev-parse --show-superproject-working-tree
        # 会输出父仓库的 working tree；非子模块则输出空。2 秒超时，失败按非子模块处理。
        r = subprocess.run(
            ["git", "-C", str(slang_path.parent), "rev-parse", "--show-superproject-working-tree"],
            capture_output=True, text=True, timeout=2,
        )
        if r.stdout.strip():
            print(
                f"[slang] 警告：{SLANG_FILE} 位于 git 子模块内。"
                f"本次修改请单独提交到子模块，否则下次 update_kb / "
                f"git submodule update --remote 会覆盖本次编辑。",
                file=sys.stderr,
            )
    except Exception:
        pass
    tmp = str(slang_path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, slang_path)
    except OSError as e:
        return "写入失败: %s" % e
    return None


def save_rag_slang(slang: str, value: str) -> str | None:
    """往统一黑话表写一条 type=rag 映射；校验失败返回错误，成功返回 None。

    value: 正式语词（字符串）。重复黑话覆盖旧值；只改 type=rag，不动课程黑话。
    拒绝覆盖其他类型：同 key 不允许从一种类型直接改成另一种。
    """
    slang = (slang or "").strip()
    value = (value or "").strip()
    if not slang:
        return "黑话词不能为空"
    if not value:
        return "正式语词不能为空"
    data = read_slang_json()
    existing = data.get(slang)
    if isinstance(existing, dict) and existing.get("type") and existing.get("type") != "rag":
        return "该黑话已映射到「%s」类型，请先删除旧映射再添加" % existing["type"]
    data[slang] = {"type": "rag", "value": value}
    return atomic_write_slang_json(data)


def delete_rag_slang(slang: str) -> str | None:
    """删除统一黑话表里一条 type=rag 映射；无此条按已删除处理。绝不动课程黑话。"""
    slang = (slang or "").strip()
    if not slang:
        return "黑话词不能为空"
    data = read_slang_json()
    entry = data.get(slang)
    if not (isinstance(entry, dict) and entry.get("type") == "rag"):
        return None
    data.pop(slang, None)
    return atomic_write_slang_json(data)


def expand_query(query: str) -> str:
    """命中词条时把正式说法追加到 query 尾部；未命中原样返回。"""
    extra = [
        formal for slang, formal in _load_slang().items()
        if slang in query and formal not in query
    ]
    return f"{query} {' '.join(extra)}" if extra else query