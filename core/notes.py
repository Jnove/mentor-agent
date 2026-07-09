"""笔记与来源的纯函数：片段预览、来源去重、FAQ 导出。"""
import re
from datetime import date


def snippet(text: str, n: int = 40) -> str:
    """把一个检索片段压成一行预览：去掉入库时拼在最前的《标题》行和 markdown 标记。"""
    body = text.split("\n", 1)[-1] if text.lstrip().startswith("《") else text
    body = re.sub(r"[#>|*`]+", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:n] + ("…" if len(body) > n else "")


def dedup_sources(hits: list[dict]) -> list[dict]:
    """同一篇文档（标题+链接相同）只保留一条，用于展示来源。"""
    seen, out = set(), []
    for h in hits:
        key = (h["title"], h["source_url"])
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out


def notes_to_markdown(notes: list[dict]) -> str:
    lines = [
        f"# 学长组 FAQ（{date.today()}）",
        "",
        "> 由学长组 Agent 自动整理，依据校网/院网官方文档，时效以官网最新版为准。",
        "",
    ]
    for i, n in enumerate(notes, 1):
        lines.append(f"## {i}. {n['q']}")
        lines += [f"- {p}" for p in n["points"]]
        if n["sources"]:
            src = " · ".join(
                f"[《{s['title']}》（{s['source_org']}）]({s['source_url']})"
                for s in n["sources"]
            )
            lines += ["", f"来源：{src}"]
        lines.append("")
    return "\n".join(lines)
