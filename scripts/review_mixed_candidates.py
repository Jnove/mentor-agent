"""审查 mixed 候选：输出证据报告，供人工圈定哪些老师真混了两位同名老师。

候选来自高赞分歧规则（正高赞说好 + 负高赞说烂）。大部分候选其实是"争议大
但单一"的老师（苏德矿、吕强等），真 mixed 的特征是评论提到不同学院 / 课程
横跨不相关学科。本脚本把**强信号候选**（跨学院自述 / 跨学科课程词）排在最前，
供人工快速圈定，输出到 data/mixed_candidates_review.md。

人工审查后，把圈定的 id 传给 import_teachers.py --mixed-ids 跑 LLM 聚类确认。

用法:
    python scripts/review_mixed_candidates.py [--limit N] [--db data/teacher.db]
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import TEACHER_DB  # noqa: E402

# 与 import_teachers.py 的 detect_mixed_candidates 高赞分歧规则保持一致
COMMENT_MIN = 10
DIVERGENT_NET = 15
DIVERGENT_MIN = 2

OUT = Path("data/mixed_candidates_review.md")

# 自述学院正则：评论里「我是 XX学院」「我说的是 XX学院的 XX老师」等
_SELF = __import__("re").compile(
    r"(我是|我说的是|我上的是|这个老师|该老师|他|她)[^。！？\n]{0,10}(学院)")


def _candidate_ids(conn) -> list[int]:
    rows = conn.execute("SELECT teacher_id, net_votes FROM comments").fetchall()
    agg: dict[int, list[int]] = {}
    for tid, net in rows:
        agg.setdefault(tid, []).append(net or 0)
    cands = []
    for tid, nets in agg.items():
        if len(nets) < COMMENT_MIN:
            continue
        pos = sum(1 for n in nets if n >= DIVERGENT_NET)
        neg = sum(1 for n in nets if n <= -DIVERGENT_NET)
        if pos >= DIVERGENT_MIN and neg >= DIVERGENT_MIN:
            cands.append(tid)
    return cands


def _strong_signal(conn, tid, name, all_text) -> str | None:
    """返回强 mixed 信号描述；无强信号返回 None。

    判定用姓名锚定，避免误报（"有两个问题"、"四校合并"都不算）：
    - 评论含「有两个/两个 + 该老师姓名」（如"浙大有两个王俊"）
    - 评论含「重名/同名/名字一样」且上下文提到老师
    """
    import re

    # 1) 姓名锚定：有两个 + 姓名 / 两个 + 姓名 / 同名重名
    if name and len(name) >= 2:
        name_hits = [
            ("有两个" + name) in all_text,
            ("两个" + name) in all_text,
            (name + "同名") in all_text,
            (name + "重名") in all_text,
            (name + "有两个") in all_text,
            ("两个同名" + name) in all_text,
            ("同名" + name) in all_text,
        ]
        if any(name_hits):
            return "评论提到同名/重名(" + name + ")"

    # 2) 跨学院自述
    colleges = set()
    for m in _SELF.finditer(all_text):
        cm = re.search(r"([一-鿿]{2,6})学院", all_text[m.start():m.end() + 10])
        if cm:
            colleges.add(cm.group(1))
    if len(colleges) >= 2:
        return "跨学院自述: " + "/".join(sorted(colleges))

    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="mixed 候选审查报告")
    ap.add_argument("--db", default=TEACHER_DB)
    ap.add_argument("--limit", type=int, default=60, help="最多输出几个候选")
    ap.add_argument("--only-strong", action="store_true", help="只输出强信号候选")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cands = _candidate_ids(conn)

    # 收集每个候选的文本 + 强信号
    strong, weak = [], []
    for tid in cands:
        t = conn.execute("SELECT name FROM teachers WHERE id=?", (tid,)).fetchone()
        tname = t["name"] if t else ""
        texts = [r[0] for r in conn.execute(
            "SELECT content FROM comments WHERE teacher_id=?", (tid,)).fetchall()]
        all_text = "".join(t or "" for t in texts)
        sig = _strong_signal(conn, tid, tname, all_text)
        (strong if sig else weak).append((tid, sig, all_text))

    print("候选总数:", len(cands), " 强信号:", len(strong), " 弱信号:", len(weak))
    ordered = strong + weak
    if args.only_strong:
        ordered = strong

    lines = ["# mixed 候选审查报告", "",
             "候选规则：评论数≥{0} 且 |净赞|≥{1} 的正负高赞都≥{2} 条。".format(
                 COMMENT_MIN, DIVERGENT_NET, DIVERGENT_MIN),
             "强信号（排前）= 评论自述不同学院 / 课程词跨学科，基本可确认 mixed；"
             "弱信号 = 高分低分都有但针对同一人（争议大），多为单一老师。",
             ""]
    for tid, sig, all_text in ordered[: args.limit]:
        t = conn.execute("SELECT id,name,college,rating_count,rating FROM teachers WHERE id=?", (tid,)).fetchone()
        if not t:
            continue
        tag = "【强信号】" if sig else ""
        lines.append("## {tag}{name} (id={id}, {college})  评分 {rating:.1f} · {rc}人".format(
            tag=tag, name=t["name"], id=t["id"], college=t["college"] or "?",
            rating=t["rating"] or 0, rc=t["rating_count"] or 0))
        if sig:
            lines.append("  信号: " + sig)
        pos = conn.execute(
            "SELECT content, net_votes FROM comments WHERE teacher_id=? AND net_votes>=? "
            "ORDER BY net_votes DESC LIMIT 2", (tid, DIVERGENT_NET)).fetchall()
        neg = conn.execute(
            "SELECT content, net_votes FROM comments WHERE teacher_id=? AND net_votes<=-? "
            "ORDER BY net_votes ASC LIMIT 2", (tid, DIVERGENT_NET)).fetchall()
        for c, n in pos:
            lines.append("  [+{0}] {1}".format(n, (c or "").strip().replace("\n", " ")[:90]))
        for c, n in neg:
            lines.append("  [{0}] {1}".format(n, (c or "").strip().replace("\n", " ")[:90]))
        lines.append("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print("报告已写入:", OUT)
    print("提示：圈定后跑 python import_teachers.py --force --mixed-ids=1,2,3")


if __name__ == "__main__":
    main()
