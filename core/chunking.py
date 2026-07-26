"""文档切块：按 ## / ### 标题切，超长小节按段落打包，超长段落逐级细切（行 → 句 → 硬切）。"""
import re

from core.config import MAX_CHUNK_CHARS

OVERLAP_LINES = 2       # 续块最多携带的重叠行数
OVERLAP_BUDGET = 80     # 重叠总字数上限：行本身很长（句子级切片）时放弃重叠
HEADING_CARRY_MAX = 80  # 标题行超过此长度就不携带（爬虫脏数据里的假标题行）

# markdown 表格分隔线的字符集；是否真是分隔线还要求同时含 - 和 |（见 _find_table_header），
# 否则纯空格行 / '---' 水平线会被误判
_TABLE_SEP_RE = re.compile(r"\s*[-:| ]+\s*")
_SENT_RE = re.compile(r"[^。！？；!?]*[。！？；!?]|[^。！？；!?]+")


def _split_long_line(line: str, limit: int) -> list[str]:
    """整段一行的长段按句切再打包（中文散文段在 markdown 里常常不换行）。"""
    pieces: list[str] = []
    buf = ""
    for sent in _SENT_RE.findall(line):
        if buf and len(buf) + len(sent) > limit:
            pieces.append(buf)
            buf = ""
        buf += sent
    if buf:
        pieces.append(buf)
    # 罕见兜底：单句仍超长（无句读的长串）按字数硬切
    out: list[str] = []
    for p in pieces:
        out.extend(p[i:i + limit] for i in range(0, len(p), limit))
    return out


def _tail_overlap(buf: list[str]) -> list[str]:
    tail: list[str] = []
    size = 0
    for line in reversed(buf):
        if len(tail) >= OVERLAP_LINES or size + len(line) > OVERLAP_BUDGET:
            break
        tail.insert(0, line)
        size += len(line)
    return tail


def _find_table_header(lines: list[str]) -> list[str] | None:
    """找表头两行（表头 + 分隔线）。表头可以不在段首：表格前常有一句引入语。"""
    for i in range(1, len(lines)):
        sep = lines[i]
        if (
            "|" in sep and "-" in sep and "|" in lines[i - 1]
            and _TABLE_SEP_RE.fullmatch(sep)
        ):
            header = lines[i - 1:i + 1]
            # 表头本身过宽就不携带，否则续块被表头挤掉正文
            if len(header[0]) + len(header[1]) <= 2 * OVERLAP_BUDGET:
                return header
            return None
    return None


def _split_long_paragraph(para: str, limit: int) -> list[str]:
    """无空行的超长段按行硬切。

    KB_FORMAT.md 要求表格写成 markdown 表格，行间没有空行，长表会整张落进这里；
    表格续块必须补上表头两行（表头 + 分隔线），否则续块里的列没有含义。
    普通文本续块带上前块末尾几行做重叠，保住跨行语义。
    """
    lines: list[str] = []
    for line in para.split("\n"):
        if len(line) > limit:
            lines.extend(_split_long_line(line, limit))
        else:
            lines.append(line)

    header = _find_table_header(lines)

    pieces: list[str] = []
    buf: list[str] = []
    size = 0  # 即 len("\n".join(buf))，增量维护，不必每行重扫缓冲区
    for line in lines:
        if buf and size + 1 + len(line) > limit:
            pieces.append("\n".join(buf))
            buf = list(header) if header else _tail_overlap(buf)
            size = sum(len(x) for x in buf) + max(0, len(buf) - 1)
        size += len(line) + (1 if buf else 0)
        buf.append(line)
    pieces.append("\n".join(buf))
    return pieces


def split_by_headings(text: str) -> list[str]:
    parts = re.split(r"(?=^#{2,3} )", text, flags=re.M)
    chunks = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) <= MAX_CHUNK_CHARS:
            chunks.append(p)
            continue

        # 小节超长：摘下标题行，打包后给每块补回——否则标题会被 flush 成孤块，
        # 而信息量最大的第二块起反而不带标题、检索命中率下降。
        # 整节只有一行（rest 为空）时不能摘，否则内容会被整节丢掉
        first_line, _, rest = p.partition("\n")
        if (
            first_line.startswith("#") and rest.strip()
            and len(first_line) <= HEADING_CARRY_MAX
        ):
            heading, body = first_line, rest
        else:
            heading, body = "", p

        # 标题是打包后才补回的，预算里先扣掉（+3 是「（续）」的量级）
        limit = max(120, MAX_CHUNK_CHARS - len(heading) - 3)
        packed: list[str] = []
        buf = ""
        for para in body.split("\n\n"):
            if not para.strip():
                continue
            for piece in (
                [para] if len(para) <= limit else _split_long_paragraph(para, limit)
            ):
                if buf and len(buf) + len(piece) > limit:
                    packed.append(buf.strip())
                    buf = ""
                buf += piece + "\n\n"
        if buf.strip():
            packed.append(buf.strip())

        # 每块都带小节标题：首块原样，续块标注（续）
        for i, c in enumerate(packed):
            if heading:
                c = f"{heading}\n{c}" if i == 0 else f"{heading}（续）\n{c}"
            chunks.append(c)
    return chunks
