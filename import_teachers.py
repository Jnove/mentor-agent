"""把评教社区数据导入 SQLite（core/teachers.py 的数据源）。

数据来自 knowledge_base/chalaoshi（UTF-8）：
- teachers.csv            10094 位老师（id/姓名/学院/热度/评分人数/评分/拼音/缩写）
- comment_<学院>.csv      135 文件 22 万+条评教评论（按 老师id 关联）
- gpa.json                6601 位老师 → [[课程名, 平均绩点, 样本数, 标准差], …]

mixed（单记录混两位同名老师）检测是两阶段：
1. 便宜规则筛候选集（A 跨学院文件 / B 评论自述学院 / C 高赞分歧），
2. 可选 LLM 趟对候选确认是否真 mixed 并按课程/学院归簇。

用法:
    python import_teachers.py
    python import_teachers.py --force          # 全量重建
    python import_teachers.py --no-llm         # 跳过混合记录 LLM 聚类趟
"""
import argparse
import csv
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from core.config import KB_DIR, TEACHER_DB  # noqa: E402
from core.teachers import JUNK_JUNK, JUNK_REVIEW, _connect, comment_quality, comment_weight, init_db  # noqa: E402

DEFAULT_SRC = str(KB_DIR / "chalaoshi")

# 高赞阈值
DIVERGENT_NET = 8   # |净赞| 达到此值算"高赞"
DIVERGENT_MIN = 2    # 正/负高赞各至少几条
COMMENT_MIN = 10     # 该老师评论至少几条才考虑分歧


def _parse_time(s: str) -> int:
    """评论时间字符串 → epoch；解析失败返回 0（按最早处理）。"""
    try:
        return int(datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S").timestamp())
    except (ValueError, AttributeError):
        return 0


def _parse_sample(s: str) -> tuple[int, int]:
    """样本数字符串 → (count, is_500plus)。"""
    s = (s or "").strip()
    if s.endswith("+") and s[:-1].isdigit():
        return int(s[:-1]), 1
    if s.isdigit():
        return int(s), 0
    return 0, 0


def import_teachers(conn, src: Path) -> int:
    # DELETE 和 INSERT 放在同一个事务里——中途崩了（CSV 损坏、磁盘满）会一起回滚，
    # 不会留下 teachers 表被清空、其它表还在的半空 DB。init_db 已用 CREATE IF NOT EXISTS
    # 保证表存在；--force 路径在 main() 里 DROP 整个 DB 再重建，不需要这里兜底。
    n = 0
    with conn:
        conn.executescript("DELETE FROM teachers;")
        with (src / "teachers.csv").open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                conn.execute(
                    "INSERT INTO teachers (id,name,college,hot,rating_count,rating,pinyin,py_init,mixed) "
                    "VALUES (?,?,?,?,?,?,?,?,0)",
                    (int(row["id"]), row["姓名"], row["学院"], int(row["热度"] or 0),
                     int(row["评分人数"] or 0), float(row["评分"] or 0),
                     row["拼音"], row["拼音缩写"]),
                )
                n += 1
    return n


def import_comments(conn, src: Path, now: int) -> tuple[int, dict]:
    """读全部 comment_*.csv，返回 (条数, {老师id: [学院,...]})。DELETE 与 INSERT 同事务。"""
    colleges_of: dict[int, list] = {}
    n = 0
    with conn:
        conn.executescript("DELETE FROM comments;")
        for f in sorted(glob.glob(str(src / "comment_*.csv"))):
            college = Path(f).name[len("comment_"):-len(".csv")]
            with open(f, encoding="utf-8", newline="") as fh:
                for row in csv.reader(fh):
                    if len(row) < 8 or row[0] == "评论id":
                        continue
                    try:
                        tid = int(row[1])
                    except ValueError:
                        continue
                    ts = _parse_time(row[3])
                    net = int(row[4]); up = int(row[5]); down = int(row[6])
                    weight = comment_weight(net, ts, now)
                    content = row[7]
                    q = comment_quality(content)
                    junk = 1 if q == JUNK_JUNK else 0
                    needs_review = 1 if q == JUNK_REVIEW else 0
                    colleges_of.setdefault(tid, []).append(college)
                    conn.execute(
                        "INSERT INTO comments "
                        "(id,teacher_id,published_at,net_votes,up,down,content,weight,cluster_id,"
                        "junk,needs_review) "
                        "VALUES (?,?,?,?,?,?,?,?,NULL,?,?)",
                        (int(row[0]), tid, ts, net, up, down, content, weight, junk, needs_review),
                    )
                    n += 1
    return n, colleges_of


def import_courses(conn, src: Path) -> tuple[int, int, int, int, int]:
    """gpa.json → courses 表。返回 (匹配, 丢弃, 条数, 信号归位, 活跃者归位)。DELETE 与 INSERT 同事务。"""
    gpa = json.loads((src / "gpa.json").read_text(encoding="utf-8"))
    pinyin2id: dict[str, list] = {}
    rating_of: dict[int, int] = {}
    with conn:
        conn.executescript("DELETE FROM courses;")
        for r in conn.execute("SELECT id, name, pinyin, py_init, rating_count FROM teachers").fetchall():
            pinyin2id.setdefault(r[1], []).append(r[0])          # 姓名（gpa key 多为中文名）
            if r[2]:
                pinyin2id.setdefault(r[2].lower(), []).append(r[0])
            if r[3]:
                pinyin2id.setdefault(r[3].lower(), []).append(r[0])
            rating_of[r[0]] = int(r[4] or 0)
        matched = dropped = rows = 0
        signal_hits = active_fallback = 0
        for key, courses in gpa.items():
            ids = pinyin2id.get(key.lower())
            if not ids and len(key) >= 3:
                import difflib
                close = difflib.get_close_matches(key.lower(), list(pinyin2id), n=1, cutoff=0.7)
                ids = pinyin2id.get(close[0]) if close else None
            if not ids:
                dropped += 1
                continue
            matched += 1
            if len(ids) == 1:
                active = ids[0]
                for c in courses:
                    rows += _insert_course(conn, active, c)
                continue
            # 同名：每门课独立按「评论提课程名」归位；无信号 fallback 活跃者
            texts = _load_comment_texts(conn, ids)
            active = max(ids, key=lambda i: rating_of.get(i, 0))
            for c in courses:
                tid = _course_owner(ids, c[0], texts, rating_of)
                if tid is None:
                    tid = active
                    active_fallback += 1
                else:
                    signal_hits += 1
                rows += _insert_course(conn, tid, c)
    return matched, dropped, rows, signal_hits, active_fallback


def _load_comment_texts(conn, ids) -> dict[int, str]:
    """同名候选的评论文本（拼接），供课程信号搜索。"""
    out = {}
    for tid in ids:
        parts = conn.execute(
            "SELECT content FROM comments WHERE teacher_id=?", (tid,)
        ).fetchall()
        out[tid] = "".join((r[0] or "") for r in parts)
    return out


def _course_owner(ids, course_name, texts, rating_of):
    """按评论提及课程信号归位：返回命中 teacher_id；无信号返回 None。"""
    if not course_name or not texts:
        return None
    needle = course_name[:4]  # 课程名前 4 字作搜索信号（"公司金融"→"公司金"）
    counts = {tid: texts.get(tid, "").count(needle) for tid in ids}
    best = max(counts, key=lambda i: (counts[i], rating_of.get(i, 0)))
    return best if counts[best] > 0 else None


def _insert_course(conn, teacher_id, c) -> int:
    count, is_plus = _parse_sample(c[2] if len(c) > 2 else "")
    gpa_v = float(c[1]) if len(c) > 1 and c[1] else None
    std_v = float(c[3]) if len(c) > 3 and c[3] else None
    conn.execute(
        "INSERT INTO courses (teacher_id,name,gpa,sample_count,sample_500plus,std) "
        "VALUES (?,?,?,?,?,?)",
        (teacher_id, c[0], gpa_v, count, is_plus, std_v),
    )
    return 1


# 自述学院正则：评论里出现「我是 XX学院」「我说的是 XX学院的 XX老师」等
_SELF = re.compile(r"(我是|我说的是|我上的是|这个老师|该老师|他|她)[^。！？\n]{0,10}(学院)")


def detect_mixed_candidates(conn, colleges_of, now: int) -> dict:
    """两阶段 mixed 检测第 1 阶段：便宜规则筛候选集。

    返回 {老师id: {"reason": "cross|self|divergent", "colleges": [学院...]}}。
    A 跨学院文件 / B 评论自述学院 / C 高赞分歧，任一命中即入候选。
    """
    candidates: dict = {}

    def add(tid, reason):
        d = candidates.setdefault(tid, {"reason": reason, "colleges": []})
        # 优先级：cross（强） > self > divergent（弱）
        order = {"cross": 3, "self": 2, "divergent": 1}
        if order.get(reason, 0) > order.get(d["reason"], 0):
            d["reason"] = reason

    for tid, colleges in colleges_of.items():
        uniq = set(colleges)
        if len(uniq) >= 2:
            add(tid, "cross")
            candidates[tid]["colleges"] = sorted(uniq)

    # B: 评论自述学院 —— 需扫全部评论内容
    for r in conn.execute("SELECT teacher_id, content FROM comments").fetchall():
        if _SELF.search(r[1] or ""):
            add(r[0], "self")

    # C: 高赞分歧 —— 该老师评论足够多，且正负高赞都达到阈值
    rows = conn.execute("SELECT teacher_id, net_votes FROM comments").fetchall()
    agg: dict[int, list[int]] = {}
    for tid, net in rows:
        agg.setdefault(tid, []).append(net or 0)
    for tid, nets in agg.items():
        if len(nets) < COMMENT_MIN:
            continue
        pos = sum(1 for n in nets if n >= DIVERGENT_NET)
        neg = sum(1 for n in nets if n <= -DIVERGENT_NET)
        if pos >= DIVERGENT_MIN and neg >= DIVERGENT_MIN:
            add(tid, "divergent")

    return candidates



def _llm_once(prompt: str) -> str | None:
    """离线轻量 LLM 调用（聚类趟用），失败返回 None。"""
    try:
        from core.llm import get_llm
        llm = get_llm()
        model = os.environ.get("LLM_MODEL_LIGHT") or os.environ.get("LLM_MODEL") or "deepseek-chat"
        res = llm.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )
        return (res.choices[0].message.content or "").strip()
    except Exception:
        print("[import_teachers] LLM 调用失败，混合记录保持规则判定", file=sys.stderr)
        return None


def llm_cluster_pass(conn, candidates: dict, teacher_names: dict, mixed_ids=None) -> set:
    """两阶段 mixed 检测第 2 阶段：对候选集跑 LLM，确认 mixed 并按课程/学院归簇。

    对每个候选老师一次调用；LLM 失败/畸形 → 按规则强信号处理：
    cross 保持 mixed=1（cluster_id 按学院文件粗分），self/divergent 恢复不标 mixed。

    返回 LLM 确认 mixed 的老师 id 集合。
    """
    import json as _json
    import re as _re

    confirmed_ids: set[int] = set()
    want = set(mixed_ids) if mixed_ids else None
    for tid in sorted(candidates):
        if want is not None and tid not in want:
            continue
        info = candidates[tid]
        # 该老师全部评论（截断）
        rows = conn.execute(
            "SELECT id, content, cluster_id FROM comments WHERE teacher_id=? ORDER BY weight DESC LIMIT 30",
            (tid,),
        ).fetchall()
        if not rows:
            continue
        lines = "\n".join("- [{cid}] {c}".format(cid=r[0], c=(r[1] or "")[:200]) for r in rows)
        name = teacher_names.get(tid, str(tid))
        prompt = (
            "下面是评教社区里名为「{name}」的老师名下的一些评论。"
            "这些评论可能混了多位同名不同院的老师，也可能都是同一个人。"
            "请判断：1) 是否明显存在两位不同老师；2) 若是，按课程/学院把评论归成 2-3 簇。"
            "只输出 JSON：{{\"mixed\": true/false, \"clusters\": [[评论id,...],...], "
            "\"labels\": [\"簇1描述\",...]}}。不要其他内容。\n\n"
            "评论列表：\n{lines}".format(name=name, lines=lines)
        )
        out = _llm_once(prompt)
        if not out:
            continue
        m = _re.search(r"\{.*\}", out, _re.DOTALL)
        if not m:
            continue
        try:
            data = _json.loads(m.group(0))
        except Exception:
            continue

        if not data.get("mixed"):
            conn.execute("UPDATE teachers SET mixed=0 WHERE id=?", (tid,))
            conn.execute("UPDATE comments SET cluster_id=NULL WHERE teacher_id=?", (tid,))
            continue

        confirmed_ids.add(tid)
        conn.execute("UPDATE teachers SET mixed=1 WHERE id=?", (tid,))
        # 归簇：评论 id → cluster index；LLM 未覆盖的评论落 NULL（查询时并入簇 0）
        clusters = data.get("clusters") or []
        cid2c = {}
        for ci, cluster in enumerate(clusters):
            if isinstance(cluster, list):
                for cid in cluster:
                    cid2c[cid] = ci
        for r in rows:
            conn.execute("UPDATE comments SET cluster_id=? WHERE id=?", (cid2c.get(r[0]), r[0]))
    return confirmed_ids


def main() -> None:
    ap = argparse.ArgumentParser(description="导入评教社区数据到 teacher.db")
    ap.add_argument("--src", default=DEFAULT_SRC, help="数据目录（默认 knowledge_base/chalaoshi）")
    ap.add_argument("--db", default=TEACHER_DB, help="目标 SQLite 路径")
    ap.add_argument("--force", action="store_true", help="全量重建（默认幂等增量）")
    ap.add_argument("--no-llm", action="store_true", help="跳过 LLM 趟（聚类+评论复核）")
    ap.add_argument("--mixed-ids", default="", help="逗号分隔圈定 mixed 候选，只对它们跑聚类")
    args = ap.parse_args()
    mixed_ids = {int(x) for x in args.mixed_ids.split(",") if x.strip()} or None

    src = Path(args.src)
    if not src.is_dir():
        print("数据目录不存在: {0}".format(src), file=sys.stderr)
        sys.exit(1)

    init_db(args.db)
    if args.force:
        # 旧 schema 可能缺 junk/sanitized 列，DROP 后按新 schema 重建
        with _connect(args.db) as conn:
            conn.executescript(
                "DROP TABLE IF EXISTS comments; DROP TABLE IF EXISTS courses; "
                "DROP TABLE IF EXISTS teachers;"
            )
        init_db(args.db)
    now = int(__import__("time").time())
    with _connect(args.db) as conn:
        n_teachers = import_teachers(conn, src)
        n_comments, colleges_of = import_comments(conn, src, now)
        gpa_ok, gpa_drop, n_courses, sig_hits, act_fb = import_courses(conn, src)

        candidates = detect_mixed_candidates(conn, colleges_of, now)
        hard_cross = sum(1 for c in candidates.values() if c["reason"] == "cross")
        # 先落强信号：cross 直接 mixed=1（其余待 LLM 确认）
        for tid, info in candidates.items():
            if info["reason"] == "cross":
                conn.execute("UPDATE teachers SET mixed=1 WHERE id=?", (tid,))

        teacher_names = {r[0]: r[1] for r in conn.execute("SELECT id,name FROM teachers").fetchall()}
        confirmed_ids: set[int] = set()
        if candidates and not args.no_llm:
            confirmed_ids = llm_cluster_pass(conn, candidates, teacher_names, mixed_ids)

        reviewed, junked = 0, 0
        if not args.no_llm:
            reviewed, junked = comment_review_pass(conn, teacher_names)

    print("teachers: {0}  comments: {1}  courses: {2}".format(
        n_teachers, n_comments, n_courses))
    print("gpa matched: {0}  gpa unmatched: {1}  (同名信号归位: {2}, 活跃者归位: {3})".format(
        gpa_ok, gpa_drop, sig_hits, act_fb))
    print("mixed candidates: {0}  (cross: {1})  LLM confirmed mixed: {2}".format(
        len(candidates), hard_cross, len(confirmed_ids)))
    print("comment review: {0}  judged junk: {1}".format(reviewed, junked))


if __name__ == "__main__":
    main()

def comment_review_pass(conn, teacher_names) -> tuple[int, int]:
    """对 needs_review=1 的边界评论跑 LLM 复核（宽松：有价值就保留）。

    每老师一次调用，传该老师全部待复核评论，返回 JSON 数组：
    [{"id": cid, "keep": true/false, "sanitized": "清洗后或null"}, ...]
    - keep=false → junk=1（纯垃圾整条滤掉）
    - keep=true + sanitized → 存清洗版（攻击片段裁剪，保留评价信息）
    - keep=true + sanitized=null → 原样保留（needs_review 清 0）
    失败/畸形 → 保留原文（宁放勿杀），只清 needs_review。

    返回 (已复核数, 判定为垃圾数)。
    """
    import json as _json
    import re as _re

    rows = conn.execute(
        "SELECT id, teacher_id, content FROM comments WHERE needs_review=1 ORDER BY teacher_id"
    ).fetchall()
    if not rows:
        return 0, 0
    by_teacher: dict[int, list] = {}
    for r in rows:
        by_teacher.setdefault(r[1], []).append(r)
    reviewed = junked = 0
    for tid, items in by_teacher.items():
        lines = "\n".join(
            "- [{cid}] {c}".format(cid=r[0], c=(r[2] or "")[:150]) for r in items
        )
        prompt = (
            "下面是评教社区里对一位老师的评论摘录。请逐条判断："
            "1) 是否纯垃圾（无信息量的脏话/纯表情/无意义，如「几把没我的大」）→ keep=false；"
            "2) 是否有价值——只要整体传递了教学/给分/课堂的真实反馈就保留（keep=true），"
            "哪怕措辞有攻击性（如「老师对学生有恶意」「根本不会讲课」）。"
            "若含攻击/脏话片段但信息有价值，给 sanitized=去掉攻击词后的清洗版；无需清洗则 null。"
            '只输出 JSON 数组：[{"id": 1, "keep": true, "sanitized": null}, ...]。不要其他内容。\n\n'
            "评论列表：\n{lines}".format(lines=lines)
        )
        out = _llm_once(prompt)
        if not out:
            for r in items:
                conn.execute("UPDATE comments SET needs_review=0 WHERE id=?", (r[0],))
            continue
        m = _re.search(r"\[.*\]", out, _re.DOTALL)
        if not m:
            for r in items:
                conn.execute("UPDATE comments SET needs_review=0 WHERE id=?", (r[0],))
            continue
        try:
            data = _json.loads(m.group(0))
        except Exception:
            for r in items:
                conn.execute("UPDATE comments SET needs_review=0 WHERE id=?", (r[0],))
            continue
        by_id = {d.get("id"): d for d in data if isinstance(d, dict)}
        for r in items:
            d = by_id.get(r[0])
            if d and d.get("keep") is False:
                conn.execute("UPDATE comments SET junk=1, needs_review=0 WHERE id=?", (r[0],))
                junked += 1
            elif d and d.get("sanitized"):
                # 清洗版存完整文本（SQLite TEXT 无长度限制），长评论不能被截断
                conn.execute(
                    "UPDATE comments SET sanitized=?, needs_review=0 WHERE id=?",
                    (str(d["sanitized"]).strip(), r[0]),
                )
            else:
                conn.execute("UPDATE comments SET needs_review=0 WHERE id=?", (r[0],))
            reviewed += 1
    return reviewed, junked
