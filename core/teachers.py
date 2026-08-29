"""查老师：结构化教师数据查询（纯 Python + SQLite，不依赖 streamlit）。

数据来自评教社区抓取，导入到 SQLite（import_teachers.py）：
- teachers 表：老师基础信息 + 混合记录标记（mixed）
- courses 表：每门课平均绩点（gpa.json，导入时按拼音/模糊匹配关联老师）
- comments 表：评教评论，weight 列导入时按「赞踩×时效」算好，查询时直接排序

查询侧（maybe_card）原则：纯数据路径永不阻塞、永不求 LLM；
LLM 只在 4 处辅助且全部 try/except 降级到纯数据（姓名抽取/重名消歧/速评/入库聚类）。
"""
from __future__ import annotations

import difflib
import functools
import html
import json
import logging
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from core.config import SLANG_FILE, TEACHER_DB, TEACHER_LOOKUP, TEACHER_SUMMARY

# 评价意图正则：问题里出现这些词才可能是「评价某老师」类提问
_INTENT_RE = re.compile(
    r"(讲得|教得|上过|上他的课|上她|的课|怎么样|好不好|给分|评分|"
    r"水不水|水吗|避雷|推荐|授课|教学|靠谱|负责|严格|评价)"
)
# 自述学院：评论里明确点名的学院归属（混合记录候选 B 规则）
_SELF_REPORT_RE = re.compile(
    r"(我是|我说的是|我上的是|这个老师|该老师)[^。！？\n]{0,12}(学院)"
)

# 脏话词表（降噪版）：数据挖掘 34 词去掉单字误伤（傻/废/滚）后 + 手工补常见漏网。
# 命中 ≠ 垃圾——句子剩余部分还有评价信息就保留（"垃圾课，讲得稀烂"），纯脏话才滤。
_BAD_WORDS = frozenset({
    "傻逼", "煞笔", "沙雕", "伞兵", "几把", "狗屁", "放屁", "脑残", "智障", "弱智",
    "脑瘫", "低能", "废物", "贱人", "蠢货", "卧槽", "操蛋", "去你妈的", "你妈",
    "妈的", "麻痹", "王八", "孙子", "婊", "贱", "滚蛋", "废狗", "垃圾人", "傻吊",
    "白痴", "呆逼", "憨批", "臭傻", "狗东西", "没脑子", "脑子有坑", "煞笔玩意",
})

# 评价信息词根：命中脏话词表后，剩余文本若含这些词（教学/给分/课堂相关），
# 视为"有内容的批评/反馈"保留；否则判纯垃圾。也用于短评豁免白名单。
_EVAL_ROOTS = frozenset({
    "讲", "教", "上", "分", "课", "师", "学", "负责", "耐心", "水", "难", "简单",
    "好", "差", "烂", "垃圾", "坑", "推", "雷", "听", "作业", "考试", "给分",
    "成绩", "点名", "答疑", "态度", "认真", "严格", "温柔", "凶", "拖堂", "压分",
    "男神", "女神", "大神", "可爱", "帅", "nice", "棒", "赞", "爽", "强", "神",
    "王", "爹", "泪", "哭", "笑", "气", "悔", "选", "退", "补", "挂", "过",
})

# 短评豁免：去符号后 1-2 字且恰好是这些词的，视为有效评价（"好""棒""水""坑"）
_SHORT_OK = frozenset({
    "好", "棒", "赞", "爽", "水", "坑", "烂", "差", "帅", "神", "强", "推", "雷",
    "男神", "女神", "大神", "可爱", "nice", "负责", "严格", "温柔", "认真",
    "垃圾", "不错", "挺好", "超好", "给力", "漂亮", "喜欢", "佩服", "呵呵", "无语",
})

# 评论质量三态：0 保留 / 1 纯垃圾（不展示）/ 2 边界待 LLM 复核
JUNK_KEEP = 0
JUNK_JUNK = 1
JUNK_REVIEW = 2


def comment_quality(content: str) -> int:
    """判定一条评论质量，返回 JUNK_KEEP / JUNK_JUNK / JUNK_REVIEW。

    纯垃圾（JUNK_JUNK）两类：
    a) 纯符号/表情/无实质内容（去符号后 <2 字，且不是白名单评价短词）
    b) 命中脏话词表 且 句子剩余部分无评价信息（"几把没我的大"）
    命中脏话词表 且 剩余有内容但难判 → JUNK_REVIEW 交由 LLM 复核。
    含实质内容的负面评价（长批评"对学生有恶意"）保留（JUNK_KEEP）。
    """
    text = (content or "").strip()
    if not text:
        return JUNK_JUNK

    # a) 去符号后长度
    stripped = re.sub(r"[\s\W_]+", "", text)
    if len(stripped) < 2:
        return JUNK_KEEP if stripped in _SHORT_OK else JUNK_JUNK

    # 命中脏话词表？
    hit_bad = [w for w in _BAD_WORDS if w in text]
    if not hit_bad:
        return JUNK_KEEP

    # 去掉所有命中的脏话词后，剩余是否还有评价信息
    rest = text
    for w in hit_bad:
        rest = rest.replace(w, "")
    rest_stripped = re.sub(r"[\s\W_]+", "", rest)
    if not rest_stripped:
        return JUNK_JUNK  # 脏话词去掉后什么都没剩：纯脏话
    if any(r in rest for r in _EVAL_ROOTS):
        return JUNK_KEEP  # 有内容批评/反馈：保留
    # 剩余仍短（凑不出有信息量的句子）→ 纯脏话；较长可能是实质批评 → 交 LLM
    return JUNK_JUNK if len(rest_stripped) < 8 else JUNK_REVIEW

_SCHEMA = """
CREATE TABLE IF NOT EXISTS teachers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  college TEXT,
  hot INTEGER,
  rating_count INTEGER,
  rating REAL,
  pinyin TEXT,
  py_init TEXT,
  mixed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY,
  teacher_id INTEGER NOT NULL REFERENCES teachers(id),
  name TEXT,
  gpa REAL,
  sample_count INTEGER,
  sample_500plus INTEGER NOT NULL DEFAULT 0,
  std REAL
);
CREATE INDEX IF NOT EXISTS idx_courses_teacher ON courses(teacher_id);
CREATE TABLE IF NOT EXISTS comments (
  id INTEGER PRIMARY KEY,
  teacher_id INTEGER NOT NULL REFERENCES teachers(id),
  published_at INTEGER NOT NULL,
  net_votes INTEGER,
  up INTEGER,
  down INTEGER,
  content TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 0,
  cluster_id INTEGER,
  junk INTEGER NOT NULL DEFAULT 0,
  sanitized TEXT,
  needs_review INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_comments_teacher ON comments(teacher_id);
CREATE INDEX IF NOT EXISTS idx_comments_w ON comments(teacher_id, cluster_id, weight DESC);
"""


@contextmanager
def _connect(db_path: str | None = None):
    path = Path(db_path or TEACHER_DB)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA journal_mode=WAL")
        with db:
            yield db
    finally:
        db.close()


def init_db(db_path: str | None = None) -> None:
    with _connect(db_path) as db:
        db.executescript(_SCHEMA)


# ---------- 名称索引（模块级缓存，导入重建后按 mtime 失效） ----------

_index_cache: dict[str, tuple] = {}  # path -> (mtime, index)


def _load_index(db_path: str | None = None) -> tuple[dict, dict, dict, dict]:
    """加载 姓名/拼音/缩写/课程名 → teacher_id 集合 的索引。

    返回 (by_name, by_pinyin, by_init, by_course)；每个值 dict[词, list[int]]。
    teacher.db 不存在/未建表（还没跑 import_teachers）时返回全空索引——
    查老师功能静默关闭，RAG 主链路不受影响。
    """
    path = str(Path(db_path or TEACHER_DB))
    mtime = os.stat(path).st_mtime if os.path.exists(path) else 0.0
    cached = _index_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    by_name: dict[str, list[int]] = {}
    by_pinyin: dict[str, list[int]] = {}
    by_init: dict[str, list[int]] = {}
    by_course: dict[str, list[int]] = {}
    try:
        with _connect(db_path) as db:
            for r in db.execute(
                "SELECT id, name, pinyin, py_init FROM teachers"
            ).fetchall():
                by_name.setdefault(r["name"], []).append(r["id"])
                if r["pinyin"]:
                    by_pinyin.setdefault(r["pinyin"].lower(), []).append(r["id"])
                if r["py_init"]:
                    by_init.setdefault(r["py_init"].lower(), []).append(r["id"])
            for r in db.execute(
                "SELECT teacher_id, name FROM courses"
            ).fetchall():
                if r["name"]:
                    by_course.setdefault(r["name"], []).append(r["teacher_id"])
    except sqlite3.Error:
        # 库缺失/未初始化：记为已知状态避免每次提问都重试
        pass
    index = (by_name, by_pinyin, by_init, by_course)
    _index_cache[path] = (mtime, index)
    return index


def _match_teacher_name(text: str, db_path: str | None = None) -> list[dict]:
    """在文本里找命中老师名（含「X老师」姓氏叫法），返回候选 {id,name,college,rating,...}。

    匹配顺序：完整姓名（最长优先，避免「王建军」被「建军」先抢）→ 姓氏+老师 → 拼音/缩写。
    返回空 = 未识别出任何老师。
    """
    by_name, by_pinyin, by_init, _ = _load_index(db_path)
    if not by_name:
        return []
    names = sorted(by_name, key=len, reverse=True)
    hits: dict[int, dict] = {}

    def add(ids: list[int]) -> None:
        for tid in ids:
            hits.setdefault(tid, {"id": tid})

    for n in names:
        if n in text:
            add(by_name[n])
            break  # 取最长的那个完整名即可，避免嵌套命中噪音
    if not hits:
        # 姓氏叫法：「李老师」「王老师」→ 找姓为李/王的（可能有多个候选）
        m = re.search(r"([一-龥]{1,2})老师", text)
        if m:
            surname = m.group(1)
            for n, ids in by_name.items():
                if n.startswith(surname) and len(n) > len(surname):
                    add(ids)
        if not hits:
            # pinyin/init are weak signals: skip when text hits a course slang (fds/ads/ds are courses, not teachers)
            if not _expand_course_slang(text, db_path):
                for tok, ids in by_pinyin.items():
                    if tok in text.lower():
                        add(ids)
                if not hits:
                    for tok, ids in by_init.items():
                        if tok in text.lower():
                            add(ids)
    if not hits:
        return []
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT id, name, college, hot, rating_count, rating, pinyin, mixed "
            "FROM teachers WHERE id IN (%s)"
            % ",".join("?" * len(hits)),
            list(hits),
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_teacher(name, db_path=None):
    """精确姓名 → 拼音(小写) → 缩写 → difflib 模糊(≥0.7)；返回候选，未知 → []。

    多候选即重名（不同学院），调用方据此消歧或反问。difflib 只对长度 ≥3 的外文名
    生效（短中文名 2 字全对才 1.0，fuzzy 无意义），并保留给 LLM 抽取出的模糊名兜底。
    """
    by_name, by_pinyin, by_init, _ = _load_index(db_path)
    name = (name or "").strip()
    if not name:
        return []
    ids = by_name.get(name) or by_pinyin.get(name.lower()) or by_init.get(name.lower())
    if ids is None and len(name) >= 3:
        all_names = list(by_name)
        close = difflib.get_close_matches(name, all_names, n=5, cutoff=0.7)
        ids = [i for n in close for i in by_name.get(n, [])]
    if not ids:
        return []
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT id, name, college, hot, rating_count, rating, pinyin, mixed "
            "FROM teachers WHERE id IN (%s)"
            % ",".join("?" * len(ids)),
            list(ids),
        ).fetchall()
    return [dict(r) for r in rows]


def comment_weight(net_votes, published_ts, now=None, lam=0.35):
    """评论权重 = 赞踩压缩 × 时效衰减。

    (1 + sign(net)·log2(1+|net|)) 对长尾净赞做对数压缩：净赞中位 2、p95 21、
    长尾到 ±900，不压缩的话个别高赞评论会统治聚合，且单人刷赞可轻易放大。
    exp(-λ·age_years)，λ=0.35 半衰期约 2 年——教学评价随时间过时，但不应瞬间清零。
    vote 下限 0.01：负赞极端时 1-log2(1+|net|) 会跌到 0 甚至为负，让净赞=-1
    的新评论 weight=0、在 ORDER BY weight DESC LIMIT 60 里被静默淘汰（被正
    评论挤出前 60），下限保证所有评论都能进入排序、靠时效衰减决胜负。
    """
    sign = 1 if net_votes >= 0 else -1
    vote = max(0.01, 1 + sign * math.log2(1 + abs(net_votes)))
    now = int(time.time()) if now is None else now
    age_years = max(0, now - published_ts) / 31557600.0
    return vote * (math.exp(-lam * age_years) if age_years > 0 else 1.0)


def get_teacher_card(teacher_id, top_n=3, now=None, db_path=None):
    """组装一张教师卡片：基础信息 + 课程 + 按权重取的 top-N 评论（分簇）。

    mixed 老师返回 clusters 列表（每簇独立评论，不融合评分）；普通老师单簇。
    """
    now = int(time.time()) if now is None else now
    with _connect(db_path) as db:
        t = db.execute(
            "SELECT id, name, college, hot, rating_count, rating, pinyin, mixed "
            "FROM teachers WHERE id=?", (teacher_id,)
        ).fetchone()
        if not t:
            return None
        t = dict(t)
        courses = [dict(r) for r in db.execute(
            "SELECT name, gpa, sample_count, sample_500plus, std FROM courses "
            "WHERE teacher_id=? ORDER BY sample_500plus DESC, sample_count DESC",
            (teacher_id,),
        ).fetchall()]
        rows = [dict(r) for r in db.execute(
            "SELECT id, published_at, net_votes, up, down, "
            "COALESCE(sanitized, content) AS content, cluster_id "
            "FROM comments WHERE teacher_id=? AND junk=0 "
            "ORDER BY weight DESC LIMIT 60",
            (teacher_id,),
        ).fetchall()]

    mixed = bool(t["mixed"])
    clusters = {}
    for r in rows:
        key = r["cluster_id"] if mixed and r["cluster_id"] is not None else 0
        clusters.setdefault(key, []).append(r)
    out_clusters = []
    for key, items in sorted(clusters.items()):
        out_clusters.append({
            "id": key,
            "caveat": mixed,
            "comments": items[:top_n],
        })
    return {
        "teacher": t,
        "courses": courses,
        "clusters": out_clusters,
        "mixed": mixed,
        "now": now,
    }


def _find_course(text, candidates, db_path=None):
    """从文本里找命中课程名（优先候选老师的课程），返回课程名或 None。"""
    _, _, _, by_course = _load_index(db_path)
    cand_ids = {c["id"] for c in candidates}
    cand_courses = [c for c, ids in by_course.items() if any(i in cand_ids for i in ids)]
    for c in sorted(cand_courses, key=len, reverse=True):
        if c in text:
            return c
    for c in sorted(by_course, key=len, reverse=True):
        if c in text:
            return c
    return None


def detect_teacher_query(text, db_path=None):
    """规则层识别「评价某老师」提问，返回 {name, course, candidates} 或 None。

    先要求文本带评价意图词（避免普通政策问题误触），再匹配老师名；
    课程名用于消歧。纯规则、不依赖 LLM。
    """
    if not _INTENT_RE.search(text):
        return None
    candidates = _match_teacher_name(text, db_path)
    if not candidates:
        return None
    course = _find_course(text, candidates, db_path)
    return {"name": candidates[0]["name"], "course": course, "candidates": candidates}


# 选课意图词：文本带这些词才可能是「选某门课求推荐老师」类提问
_COURSE_INTENT_RE = re.compile(
    r"(选|选课|上|哪(?:个|些|位|几)|哪位|推荐|比较好|怎么选|谁的|好吗|哪个老师|怎么样|如何)"
)


def _literal_course_names(text, db_path=None):
    """文本里直接写出的完整课程名（用于判断多门课是否已被用户收窄到一门）。"""
    _, _, _, by_course = _load_index(db_path)
    return [c for c in by_course if c in text]


def course_card(course, db_path=None):
    """按单一课程名反查老师的选课卡片（course_choose 确认具体哪门课后调用）。"""
    cq = _course_candidates(course, db_path)
    if cq is None:
        return {"kind": "course", "course": course, "courses": [course], "candidates": []}
    return {"kind": "course", "course": cq["course"], "courses": cq["courses"],
            "candidates": _sort_course_candidates(cq["candidates"])}


def detect_course_query(text, db_path=None):
    """识别「选某门课求推荐老师」的提问，返回 {course, candidates} 或 None。

    规则：文本带选课意图词 + 能从课程表匹配到课程名。纯数据，无 LLM。
    匹配顺序：课程名精确 → 课程名前 4 字（长度 ≥4 的课程词才启用，防误伤）。
    candidates 为 [{id, name, college, rating, rating_count, courses:[课程名]}...]，
    同一老师教多门同名课只保留一次。
    """
    if not _COURSE_INTENT_RE.search(text):
        return None
    _, _, _, by_course = _load_index(db_path)
    if not by_course:
        return None

    # 1) 黑话展开（fds→数据结构基础），一对一/一对多；与原文匹配取并集
    slang_courses = _expand_course_slang(text, db_path) or []

    # 2) 原文精确匹配
    exact = [c for c in by_course if c in text]

    # 3) 前 4 字兜底（仅当黑话和精确都没命中时）
    if not slang_courses and not exact:
        words = sorted(by_course, key=len)
        hits = set()
        for c in words:
            if len(c) < 4:
                continue
            prefix = c[:4]
            if prefix in text:
                hits.add(c)
        if not hits:
            return None
        if len(hits) > 20:
            return None  # 命中过多说明词太短，宁可走 RAG
        return _course_candidates(max(hits, key=len), db_path)

    # 合并黑话展开 + 原文精确命中，去重
    courses = list(dict.fromkeys([*slang_courses, *exact]))
    return _course_candidates(courses, db_path)


def _load_course_slang():
    """加载课程黑话（knowledge_base/slang.json 中 type=course 的条目）；缺失/损坏 → {}。"""
    import json as _json
    try:
        with open(SLANG_FILE, encoding="utf-8") as f:
            data = _json.load(f)
        out = {}
        for k, entry in data.items():
            if isinstance(entry, dict) and entry.get("type") == "course":
                value = entry.get("value")
                if isinstance(value, list) and value:
                    out[k] = [c for c in value if c]
        return out
    except (OSError, ValueError):
        return {}


def _expand_course_slang(text, db_path=None):
    """文本里命中黑话 → 返回展开后的课程名列表（可能来自多个黑话）；未命中 → None。

    只返回课程表里真实存在的课程名；一对多全部展开。
    最长词优先去重：命中的黑话若有包含关系（ds 是 ads 的子串），只取更长的那个，
    避免 "ads" 同时展开 "ds" 造成重复。
    """
    slang = _load_course_slang()
    if not slang:
        return None
    hits = []
    for key, courses in slang.items():
        start = 0
        while True:
            i = text.find(key, start)
            if i < 0:
                break
            hits.append((len(key), i, i + len(key), courses))
            start = i + len(key)
    if not hits:
        return None
    hits.sort(key=lambda h: (-h[0], h[1]))  # 最长优先，等长靠前
    chosen, occupied = [], []
    for length, s, e, courses in hits:
        if any(s < oe and e > os for os, oe in occupied):
            continue  # 与更长命中重叠：跳过
        occupied.append((s, e))
        chosen.extend(c for c in courses if c)
    if not chosen:
        return None
    _, _, _, by_course = _load_index(db_path)
    return [c for c in chosen if c in by_course] or None


def _course_slang_course_exists(course, db_path=None) -> bool:
    """校验课程名在 courses 表里存在（admin 添加黑话时防手滑）。"""
    _, _, _, by_course = _load_index(db_path)
    return course in by_course


def save_course_slang(slang, courses, db_path=None) -> str | None:
    """往统一黑话表（knowledge_base/slang.json）追加/更新一条 type=course 映射；校验失败返回错误，成功返回 None。

    courses: 课程名列表；每个必须在 courses 表存在，否则整个拒绝（不写半条）。
    拒绝覆盖其他类型（RAG 等已有映射）：同 key 不允许从一种类型直接改成另一种，
    防止 admin 误操作把 RAG 黑话静默改成课程黑话。
    重复黑话覆盖旧值；空黑话/空课程拒绝。只改 type=course 的条目，不动 RAG 黑话。
    """
    import json as _json
    slang = (slang or "").strip()
    courses = [c.strip() for c in courses if c and c.strip()]
    if not slang:
        return "黑话词不能为空"
    if not courses:
        return "至少需要一个正式课程名"
    for c in courses:
        if not _course_slang_course_exists(c, db_path):
            return "课程《%s》不在评教课程表里，无法建立映射" % c
    try:
        with open(SLANG_FILE, encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    existing = data.get(slang)
    if isinstance(existing, dict) and existing.get("type") and existing.get("type") != "course":
        return "该黑话已映射到「%s」类型，请先删除旧映射再添加" % existing["type"]
    data[slang] = {"type": "course", "value": courses}
    err = _atomic_write_slang(data)
    return err


def delete_course_slang(slang, db_path=None) -> str | None:
    """往统一黑话表（knowledge_base/slang.json）删除一条 type=course 映射；文件缺失/损坏按无此条处理。

    只删 type=course 条目，绝不动 RAG 黑话。删除后仍保留其余全部条目（含 RAG）。
    """
    import json as _json
    slang = (slang or "").strip()
    if not slang:
        return "黑话词不能为空"
    try:
        with open(SLANG_FILE, encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    entry = data.get(slang)
    if not (isinstance(entry, dict) and entry.get("type") == "course"):
        return None  # 无此课程黑话，视为已删除
    data.pop(slang, None)
    return _atomic_write_slang(data)


def _atomic_write_slang(data) -> str | None:
    """原子写 SLANG_FILE：写临时文件后 os.replace 改名，避免崩在 dump 中途留下半截 JSON。
    写入前检测 SLANG_FILE 是否落在 git 子模块里——如果是，提示管理员需要单独
    提交子模块，否则下次 update_kb / git submodule update --remote 会覆盖本地的修改。
    """
    import json as _json
    import os as _os
    import sys as _sys
    from pathlib import Path as _Path
    slang_path = _Path(SLANG_FILE)
    try:
        # 子模块检测：git submodule 目录里跑 git rev-parse --show-superproject-working-tree
        # 会输出父仓库的 working tree；非子模块则输出空。2 秒超时，失败按非子模块处理。
        import subprocess as _sp
        r = _sp.run(
            ["git", "-C", str(slang_path.parent), "rev-parse", "--show-superproject-working-tree"],
            capture_output=True, text=True, timeout=2,
        )
        if r.stdout.strip():
            print(
                f"[slang] 警告：{SLANG_FILE} 位于 git 子模块内。"
                f"本次修改请单独提交到子模块，否则下次 update_kb / "
                f"git submodule update --remote 会覆盖本次编辑。",
                file=_sys.stderr,
            )
    except Exception:
        pass
    tmp = str(slang_path) + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(data, f, ensure_ascii=False, indent=2)
        _os.replace(tmp, slang_path)
    except OSError as e:
        return "写入失败: %s" % e
    return None


def _course_candidates(courses, db_path=None):
    """按课程名列表反查老师，聚合去重（同一老师教多门课合并 courses）。

    courses: 单个课程名或课程名列表。返回 {course: 主名, courses: 全部课程, candidates}。
    """
    if isinstance(courses, str):
        courses = [courses]
    if not courses:
        return None
    with _connect(db_path) as db:
        ph = ",".join("?" * len(courses))
        rows = db.execute(
            "SELECT DISTINCT c.teacher_id, t.name, t.college, t.rating, t.rating_count "
            "FROM courses c JOIN teachers t ON t.id=c.teacher_id "
            "WHERE c.name IN (%s) ORDER BY t.rating_count DESC" % ph,
            tuple(courses),
        ).fetchall()
        by_teacher = {}
        for r in rows:
            by_teacher.setdefault(r[0], {
                "id": r[0], "name": r[1], "college": r[2],
                "rating": float(r[3] or 0), "rating_count": int(r[4] or 0),
                "courses": [],
            })
        for tid in by_teacher:
            taught = [x[0] for x in db.execute(
                "SELECT DISTINCT name FROM courses WHERE teacher_id=? AND name IN (%s)" % ph,
                (tid,) + tuple(courses),
            ).fetchall()]
            by_teacher[tid]["courses"] = taught
    if not by_teacher:
        return None
    return {"course": max(courses, key=len), "courses": courses,
            "candidates": list(by_teacher.values())}


def _sort_course_candidates(cands):
    """评分人数 >0 的按评分降序；人数 0 的排最后（显示「暂无评分」）。"""
    return sorted(cands, key=lambda c: (c["rating_count"] > 0, c["rating"]), reverse=True)


def _llm_call(llm, content):
    """一次轻量模型调用，返回文本；失败返回 None。"""
    try:
        model = os.environ.get("LLM_MODEL_LIGHT") or os.environ.get("LLM_MODEL") or "deepseek-chat"
        res = llm.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": content}],
            stream=False,
        )
        return (res.choices[0].message.content or "").strip()
    except Exception:
        logging.exception("teacher LLM 辅助调用失败，降级纯数据")
        return None


def llm_extract_teacher(llm, text):
    """(a) 从口语化提问里抽老师姓名+课程；失败/无老师 → None。"""
    prompt = (
        "下面是同学的一句话，判断他是否在询问某位老师的教学评价。"
        "若是，抽取老师姓名（可能口语化/只给姓/用课程指代）与提到的课程（如有）。"
        '只输出 JSON：{"is_teacher": true, "name": "洪鑫", "course": "微积分"} '
        '或 {"is_teacher": false}。不要任何其他内容。\n\n'
        "同学的话：" + text
    )
    out = _llm_call(llm, prompt)
    if not out:
        return None
    m = re.search(r"\{.*\}", out, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except Exception:
        return None
    if not data.get("is_teacher"):
        return None
    name = str(data.get("name") or "").strip()
    if not name:
        return None
    course = str(data.get("course") or "").strip() or None
    return {"name": name, "course": course}


def llm_disambiguate(llm, candidates, question):
    """(b) 多候选时让 LLM 结合提问上下文选出最可能那位，返回 teacher_id 或 None。"""
    listing = "\n".join(
        "- {id}: {name}（{college}，评分 {rating:.1f}）".format(
            id=c["id"], name=c["name"], college=c["college"], rating=float(c["rating"] or 0))
        for c in candidates
    )
    prompt = (
        "同学问：\n" + question + "\n\n"
        "数据库里有以下同名老师候选：\n" + listing + "\n\n"
        "根据提问里提到的学院/课程/专业等线索，判断最可能是哪位。"
        "只输出候选编号（id 数字），不要其他内容。"
    )
    out = _llm_call(llm, prompt)
    if not out:
        return None
    m = re.search(r"\d+", out)
    if not m:
        return None
    tid = int(m.group(0))
    if any(c["id"] == tid for c in candidates):
        return tid
    return None


_SUMMARY_CACHE = {}


def llm_summary(llm, card, db_path=None):
    """(c) 2-3 句速评；按 teacher_id 缓存，失败降级为 None（卡片直接展示评论）。"""
    if not TEACHER_SUMMARY:
        return None
    tid = card["teacher"]["id"]
    cached = _SUMMARY_CACHE.get(tid)
    if cached is not None:
        return cached
    t = card["teacher"]
    top_comments = []
    for cl in card["clusters"]:
        for c in cl["comments"]:
            top_comments.append(c["content"])
    body = "\n".join("- " + c[:120] for c in top_comments[:6])
    prompt = (
        "基于下面这位老师的评教评论，写 2-3 句中文速评，帮助同学判断是否选他的课。"
        "包含：教学风格、给分情况、值得注意的点。语气中立客观，不夸大。"
        "只输出速评本身，不要标题和列举。\n\n"
        "老师：{name}（{college}），评分 {rating:.1f}/10，{count} 人评价\n"
        "高赞评论摘录：\n{body}".format(
            name=t["name"], college=t["college"] or "未知",
            rating=float(t["rating"] or 0), count=int(t["rating_count"] or 0),
            body=body)
    )
    out = _llm_call(llm, prompt)
    if not out:
        _SUMMARY_CACHE[tid] = None
        return None
    _SUMMARY_CACHE[tid] = out
    return out


def _not_found_card(name):
    return {"kind": "not_found", "name": name}


def _finalize(card, det_name, llm, db_path):
    """给组装好的卡片补 name 与速评（(c)，失败不阻塞）。"""
    if card is None:
        return _not_found_card(det_name)
    card["name"] = det_name
    if llm is not None:
        s = llm_summary(llm, card, db_path)
        if s:
            card["summary"] = s
    return card


def maybe_card(text, question, llm=None, db_path=None):
    """编排入口：识别老师提问 → 组装卡片 dict；不是老师提问/纯数据失败 → None。

    规则层 detect 先行；规则层失败且给 LLM 时，用 LLM 抽姓名兜底（(a)）。
    重名多候选：课程/学院消歧（(b) LLM 兜底），仍歧义 → 返回"请补充学院/课程"卡片。
    """
    if not TEACHER_LOOKUP:
        return None
    det = detect_teacher_query(text, db_path)
    if det is None:
        # 规则层没认出老师 → 试课程反查（选课推荐），纯数据、先于 LLM
        cq = detect_course_query(text, db_path)
        if cq:
            multi = cq.get("courses") or [cq["course"]]
            literal = _literal_course_names(text, db_path)
            # 黑话一对多（数分→5 门）展开出多门课、且用户没直接写出完整课程名时，
            # 先让用户确认具体哪一门，避免把多门课的老师混进同一张卡
            if len(multi) > 1 and not any(c in literal for c in multi):
                return {"kind": "course_choose", "courses": multi}
            return {"kind": "course", "course": cq["course"],
                    "courses": multi,
                    "candidates": _sort_course_candidates(cq["candidates"])}
    if det is None and llm is not None:
        ex = llm_extract_teacher(llm, text)
        if ex:
            cands = resolve_teacher(ex["name"], db_path)
            if not cands:
                return _not_found_card(ex["name"])
            det = {"name": ex["name"], "course": ex.get("course"), "candidates": cands}
    if det is None:
        return None
    candidates = det["candidates"]
    if len(candidates) == 1:
        card = get_teacher_card(candidates[0]["id"], db_path=db_path)
        return _finalize(card, candidates[0]["name"], llm, db_path)

    # 多候选：先用规则（课程/学院）收窄
    course = det.get("course")
    college = None
    m = re.search(r"([一-龥]{2,6})(?:学院|学系)", text)
    if m:
        college = m.group(1)
    narrowed = []
    for c in candidates:
        if course and _candidate_teaches(c["id"], course, db_path):
            narrowed.append(c)
            continue
        if college and c["college"] and college in c["college"]:
            narrowed.append(c)
    if len(narrowed) == 1:
        card = get_teacher_card(narrowed[0]["id"], db_path=db_path)
        return _finalize(card, narrowed[0]["name"], llm, db_path)

    # LLM 兜底（(b)）
    if llm is not None:
        tid = llm_disambiguate(llm, candidates, question)
        if tid is not None:
            card = get_teacher_card(tid, db_path=db_path)
            if card is not None:
                return _finalize(card, card["teacher"]["name"], llm, db_path)

    return {"kind": "ambiguous", "candidates": candidates, "name": det["name"]}


def _candidate_teaches(teacher_id, course, db_path=None):
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT 1 FROM courses WHERE teacher_id=? AND name=? LIMIT 1",
            (teacher_id, course),
        ).fetchone()
    return row is not None



def _fmt_net(net):
    if net > 0:
        return '<span class="tc-vote tc-vote-pos">+{}</span>'.format(net)
    if net < 0:
        return '<span class="tc-vote tc-vote-neg">{}</span>'.format(net)
    return '<span class="tc-vote tc-vote-zero">0</span>'


def render_card_html(card):
    """把卡片 dict 渲染成结构化 HTML（CSS 类见 ui/theme.py .teacher-card 族）。"""
    if card.get("kind") == "not_found":
        name = html.escape(card.get("name") or "")
        return (
            '<div class="teacher-card">'
            '<div class="tc-head"><span class="tc-name">{}</span>'
            '<span class="tc-college">尚未收录</span></div>'
            '<div class="tc-notfound">该老师尚未收录（可能不在评教社区内），'
            '暂无评分与评价。</div></div>'.format(name)
        )
    if card.get("kind") == "ambiguous":
        name = html.escape(card.get("name") or "")
        rows = "".join(
            '- <b>{}</b>（{}，评分 {:.1f}）<br/>'.format(
                html.escape(c["name"]), html.escape(c["college"] or "学院未知"),
                float(c["rating"] or 0))
            for c in card.get("candidates", [])
        )
        return (
            '<div class="teacher-card">'
            '<div class="tc-head"><span class="tc-name">{}</span></div>'
            '<div class="tc-ambiguous">查到多位同名老师，请补充学院或课程以便确认：'
            '<br/>{}</div></div>'.format(name, rows)
        )

    if card.get("kind") == "course_choose":
        return (
            '<div class="teacher-card">'
            '<div class="tc-head"><span class="tc-name">选课推荐</span></div>'
            '<div class="tc-choose">这个叫法可能对应多门课，请选择你具体要问哪一门：</div>'
            '</div>'
        )

    if card.get("kind") == "course":
        course = html.escape(card.get("course") or "")
        cands = card.get("candidates", [])
        if not cands:
            return (
                '<div class="teacher-card">'
                '<div class="tc-head"><span class="tc-name">{}</span>'
                '<span class="tc-college">选课推荐</span></div>'
                '<div class="tc-notfound">暂未收录教这门课的老师。</div></div>'.format(course)
            )
        rows = ""
        for c in cands:
            rating = float(c["rating"] or 0)
            rc = int(c["rating_count"] or 0)
            if rc == 0:
                score = "暂无评分"
            else:
                score = "{:.1f}/10（{}人）".format(rating, rc)
            low = " <em>样本少</em>" if 0 < rc < 10 else ""
            rows += (
                '<div class="tc-course"><span><b>{}</b> · {}</span>'
                '<span class="tc-gpa">{}{}</span></div>'.format(
                    html.escape(c["name"]), html.escape(c["college"] or "学院未知"),
                    score, low)
            )
        courses = card.get("courses") or []
        expand = ""
        if len(courses) > 1:
            expand = '<div class="tc-meta" style="margin:.1rem 0 .5rem">涵盖：{}</div>'.format(
                " · ".join(html.escape(x) for x in courses))
        return (
            '<div class="teacher-card">'
            '<div class="tc-head"><span class="tc-name">{course}</span>'
            '<span class="tc-college">选课推荐</span></div>'
            '{expand}'
            '<div class="tc-courses-title">教这门课的老师（按评分）</div>'
            '<div class="tc-courses">{rows}</div>'
            '<div class="tc-notfound" style="font-size:.78rem">评教社区数据，仅供参考</div>'
            '</div>'.format(course=course, expand=expand, rows=rows)
        )

    t = card["teacher"]
    name = html.escape(t["name"])
    college = html.escape(t["college"] or "未知学院")
    rating = float(t["rating"] or 0)
    rcount = int(t["rating_count"] or 0)
    hot = int(t["hot"] or 0)

    courses_html = ""
    for c in card["courses"][:5]:
        sample = "500+" if c["sample_500plus"] else str(int(c["sample_count"] or 0))
        courses_html += (
            '<div class="tc-course"><span>{}</span>'
            '<span class="tc-gpa">GPA {:.2f} <em>{}人</em></span></div>'.format(
                html.escape(c["name"] or ""), float(c["gpa"] or 0), sample)
        )
    if courses_html:
        courses_html = '<div class="tc-courses-title">课程平均绩点</div><div class="tc-courses">' + courses_html + "</div>"

    clusters_html = ""
    for cl in card["clusters"]:
        for c in cl["comments"]:
            ts = c["published_at"]
            date_s = time.strftime("%Y-%m", time.localtime(ts)) if ts else ""
            meta = "{} · {}".format(date_s, _fmt_net(int(c["net_votes"] or 0))) if date_s else _fmt_net(int(c["net_votes"] or 0))
            # 评教社区原文把换行存成字面 "\n"（反斜杠+n 两个字符），先还原为真实换行，
            # 再压缩连续换行（原文常有多余 \n\n\n），最后转成 <br> 让换行真正显示
            content = (c["content"] or "").replace("\\n", "\n")
            content = re.sub(r"\n{2,}", "\n", content).strip()
            rendered = html.escape(content).replace("\n", "<br>")
            clusters_html += (
                '<div class="tc-comment"><p>{}</p>'
                '<span class="tc-meta">{}</span></div>'.format(
                    rendered, meta)
            )
    if not clusters_html:
        clusters_html = '<div class="tc-nocomment">暂无评教评论</div>'
    else:
        clusters_html = '<div class="tc-comments-title">同学评价</div><div class="tc-comments">' + clusters_html + "</div>"

    caveat = (
        '<div class="tc-caveat">⚠ 该记录可能含多位同名老师，以下按课程分组仅供参考</div>'
        if card.get("mixed") else ""
    )
    summary = ""
    if card.get("summary"):
        summary = '<div class="tc-summary">{}</div>'.format(html.escape(card["summary"]))

    return (
        '<div class="teacher-card">'
        '<div class="tc-head"><span class="tc-name">{name}</span>'
        '<span class="tc-college">{college}</span></div>'
        '<div class="tc-stats">'
        '<div class="tc-score"><b>{rating:.1f}</b><i>/10</i></div>'
        '<div class="tc-stat">评分 <b>{rcount}</b> 人</div>'
        '<div class="tc-stat">热度 <b>{hot}</b></div>'
        '</div>'
        '{summary}{caveat}{courses_html}{clusters_html}'
        '</div>'.format(
            name=name, college=college, rating=rating, rcount=rcount, hot=hot,
            summary=summary, caveat=caveat,
            courses_html=courses_html, clusters_html=clusters_html,
        )
    )
