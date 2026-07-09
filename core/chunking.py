"""文档切块：按 ## / ### 标题切，超长块再按段落切。"""
import re

from core.config import MAX_CHUNK_CHARS


def split_by_headings(text: str) -> list[str]:
    parts = re.split(r"(?=^#{2,3} )", text, flags=re.M)
    chunks = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= MAX_CHUNK_CHARS:
            chunks.append(p)
        else:
            buf = ""
            for para in p.split("\n\n"):
                if len(buf) + len(para) > MAX_CHUNK_CHARS and buf:
                    chunks.append(buf.strip())
                    buf = ""
                buf += para + "\n\n"
            if buf.strip():
                chunks.append(buf.strip())
    return chunks
