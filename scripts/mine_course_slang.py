"""挖掘评论里的课程黑话候选：输出清单供人工确认后加入 knowledge_base/slang.json（type=course）。

自动挖掘命中率低、误报会污染映射，因此本脚本只产出候选清单，不自动写入。
两类信号：
- 英文缩写：评论里高频的 2-5 位小写字母词（排除常见英文 stoplist），
  人工对照课程名判断（fds→数据结构基础、ads→高级数据结构）。
- 中文简称：评论里出现、且与 courses.name 有「压缩关系」的 2-4 字词
  （如"数分"是"数学分析"的简称）——用前缀命中课程名来发现。

输出: data/course_slang_candidates.md

用法:
    python scripts/mine_course_slang.py [--db data/teacher.db] [--limit N]
"""
import argparse
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import TEACHER_DB  # noqa: E402

OUT = Path("data/course_slang_candidates.md")

# 常见英文/口语词，不是课程缩写
_EN_STOP = {
    "the", "and", "you", "good", "this", "that", "with", "from", "have",
    "your", "for", "not", "but", "was", "are", "his", "her", "she", "they",
    "will", "just", "like", "about", "what", "when", "then", "there",
    "very", "really", "much", "many", "more", "some", "been", "also",
    "because", "though", "should", "would", "could", "know", "think",
    "please", "sorry", "okay", "ok", "yes", "no", "hi", "hello", "thanks",
    "thank", "hope", "help", "nice", "good", "cool", "doge", "hhh", "emmm",
    "bushi", "nmsl", "yyds", "jpg", "com", "www", "ppt", "pdf", "qaq", "orz",
    "dl", "y", "o", "s", "i", "t", "h", "a", "c", "x", "r", "g", "e", "p",
}

# 英文缩写过滤：至少出现 N 次，且不像普通单词（含多个连续辅音 或 以常见词缀结尾）
def _looks_abbrev(w: str, n: int) -> bool:
    if w in _EN_STOP or len(w) < 2 or n < 5:
        return False
    # 课程缩写常为小写、多辅音、无常见元音词尾
    if re.fullmatch(r"[aeiouy]*", w):
        return False
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="挖掘课程黑话候选")
    ap.add_argument("--db", default=TEACHER_DB)
    ap.add_argument("--limit", type=int, default=60, help="英文缩写候选上限")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    course_names = [r[0] for r in conn.execute(
        "SELECT DISTINCT name FROM courses WHERE name!=''").fetchall()]

    # 英文缩写
    en_counter: Counter = Counter()
    ab_evidence: dict[str, list] = {}
    rows = conn.execute("SELECT content FROM comments").fetchall()
    for (c,) in rows:
        cc = (c or "").lower()
        for m in re.findall(r"[a-z]{2,5}", cc):
            en_counter[m] += 1
            if len(ab_evidence.setdefault(m, [])) < 2:
                ab_evidence[m].append((c or "")[:60])

    lines = ["# 课程黑话候选清单", "",
             "本文件由 scripts/mine_course_slang.py 生成，供人工确认。",
             "确认后请把候选加入 knowledge_base/slang.json（type=course，或管理后台「黑话管理」页），",
             "课程名必须与 courses 表完全一致。", ""]

    # 英文缩写段
    lines.append("## 英文缩写（评论高频 2-5 位小写词）")
    lines.append("| 词 | 次数 | 疑似课程 | 证据评论 |")
    lines.append("|---|---|---|---|")
    for w, n in en_counter.most_common(args.limit * 3):
        if not _looks_abbrev(w, n):
            continue
        # 疑似课程：课程名里含该缩写（大小写不敏感）
        guess = [c for c in course_names if w.lower() in c.lower()][:2]
        guess_s = "、".join(guess) if guess else "？"
        ev = ab_evidence.get(w, [""])[0].replace("|", "／")[:40]
        lines.append(f"| {w} | {n} | {guess_s} | {ev} |")

    # 中文简称段：2-4 字词，前缀命中课程名
    lines.append("")
    lines.append("## 中文简称（2-4 字，前缀命中课程名）")
    lines.append("| 词 | 次数 | 疑似课程 | 证据评论 |")
    lines.append("|---|---|---|---|")
    zh_counter: Counter = Counter()
    zh_evidence: dict[str, list] = {}
    for (c,) in rows:
        cc = c or ""
        # 抽取连续中文片段，取 2-4 字
        for m in re.findall(r"[一-龥]{2,4}", cc):
            zh_counter[m] += 1
            if len(zh_evidence.setdefault(m, [])) < 2:
                zh_evidence[m].append(cc[:60])
    for w, n in zh_counter.most_common(args.limit * 5):
        if n < 5:
            continue
        # 前缀命中：课程名以该词开头（数分→数学分析 不是前缀，但 2 字简称常是前两字）
        guess = [c for c in course_names if c.startswith(w) or w in c[:4]][:2]
        if not guess:
            continue
        ev = zh_evidence.get(w, [""])[0].replace("|", "／")[:40]
        lines.append(f"| {w} | {n} | {'、'.join(guess)} | {ev} |")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("候选清单已写入:", OUT)
    print("提示：确认后加入 knowledge_base/slang.json（type=course）或管理后台「黑话管理」页")


if __name__ == "__main__":
    main()
