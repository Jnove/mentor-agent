"""core/teachers.py 纯逻辑测试（不依赖 streamlit，不真实调用 LLM）。

用法: python tests/test_teachers.py
"""
import os
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlite3

from core import teachers


def _fresh_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    teachers.init_db(path)
    conn = sqlite3.connect(path)
    conn.executescript("""
    INSERT INTO teachers (id,name,college,hot,rating_count,rating,pinyin,py_init,mixed) VALUES
      (1,'洪鑫','经济学院',11733,74,9.73,'hongxin','hx',0),
      (2,'洪鑫','化学系',8000,47,9.72,'hongxin','hx',0),
      (3,'李一','数学科学学院',500,10,8.5,'liyi','ly',0),
      (4,'王晓明','计算机科学与技术学院',120,5,7.0,'wangxiaoming','wxm',1);
    INSERT INTO courses (teacher_id,name,gpa,sample_count,sample_500plus,std) VALUES
      (1,'计量经济学',4.2,86,0,0.5),
      (1,'微观经济学',3.9,150,0,0.6),
      (2,'有机化学',3.6,200,0,0.7),
      (3,'线性代数',3.8,50,0,0.4);
    INSERT INTO comments (id,teacher_id,published_at,net_votes,up,down,content,weight,cluster_id) VALUES
      (1,1,1700000000,81,100,19,'讲课很清晰，非常推荐',10.0,NULL),
      (2,1,1600000000,-28,18,46,'废话有点多',2.0,NULL),
      (3,2,1680000000,50,60,10,'有机教得特别好',5.0,NULL),
      (4,4,1700000000,30,40,10,'我是建工学院的学生，这老师教混凝土',4.0,0),
      (5,4,1690000000,20,30,10,'我说的是经管学院的这位，给分高',3.0,1),
      (6,4,1680000000,15,20,5,'乱评，别的系的同学别被误导',2.0,0);
    """)
    conn.commit()
    conn.close()
    return path


def _cleanup(db: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(db + suffix).unlink(missing_ok=True)


def test_comment_weight_monotonic():
    now = 1700000000
    # 同龄：负净赞 < 中性 < 正
    neg = teachers.comment_weight(-50, now - 365 * 86400, now)
    neu = teachers.comment_weight(0, now - 365 * 86400, now)
    pos = teachers.comment_weight(50, now - 365 * 86400, now)
    assert neg < neu < pos
    # 同赞：越新越大
    older = teachers.comment_weight(50, now - 3 * 365 * 86400, now)
    assert older < pos
    # 0 票 = 纯时效衰减
    assert 0 < teachers.comment_weight(0, now - 365 * 86400, now) < 1


def test_resolve_teacher():
    db = _fresh_db()
    # 精确姓名 → 两个候选（同名跨学院）
    cands = teachers.resolve_teacher("洪鑫", db)
    assert {c["id"] for c in cands} == {1, 2}
    # 拼音大小写不敏感
    assert {c["id"] for c in teachers.resolve_teacher("HONGXIN", db)} == {1, 2}
    # 缩写
    assert {c["id"] for c in teachers.resolve_teacher("ly", db)} == {3}
    # 未知
    assert teachers.resolve_teacher("不存在老师", db) == []
    _cleanup(db)


def test_detect_teacher_query():
    db = _fresh_db()
    # 老师评价意图 → 命中
    det = teachers.detect_teacher_query("洪鑫的计量经济学讲得怎么样？", db)
    assert det and det["name"] == "洪鑫"
    assert det["course"] == "计量经济学"
    # 非老师问题 → None
    assert teachers.detect_teacher_query("转专业需要什么条件？", db) is None
    # 提到老师但非评价意图 → None
    assert teachers.detect_teacher_query("请问洪鑫老师住哪栋楼", db) is None
    _cleanup(db)


def test_get_teacher_card_single():
    db = _fresh_db()
    card = teachers.get_teacher_card(1, db_path=db)
    assert card["teacher"]["name"] == "洪鑫"
    assert card["mixed"] is False
    # 课程按样本降序：微观(150) 在 计量(86) 前
    assert [c["name"] for c in card["courses"]] == ["微观经济学", "计量经济学"]
    # top-N 评论按 weight 排序取前 3
    assert len(card["clusters"][0]["comments"]) == 2  # 该老师只有 2 条
    assert card["clusters"][0]["comments"][0]["net_votes"] == 81
    _cleanup(db)


def test_get_teacher_card_mixed():
    db = _fresh_db()
    card = teachers.get_teacher_card(4, db_path=db)
    assert card["mixed"] is True
    # 两个簇（cluster 0 和 1）
    assert len(card["clusters"]) == 2
    _cleanup(db)


def test_render_card_html():
    db = _fresh_db()
    card = teachers.get_teacher_card(1, db_path=db)
    html = teachers.render_card_html(card)
    assert "洪鑫" in html
    assert "计量经济学" in html
    assert "/10" in html
    assert "tc-vote-pos" in html
    assert "tc-caveat" not in html  # 非 mixed 无提示
    # not_found 卡片
    nf = teachers.render_card_html({"kind": "not_found", "name": "陈晓华"})
    assert "尚未收录" in nf
    # ambiguous 卡片
    amb = teachers.render_card_html({
        "kind": "ambiguous", "name": "洪鑫", "candidates": [
            {"name": "洪鑫", "college": "经济学院", "rating": 9.7},
            {"name": "洪鑫", "college": "化学系", "rating": 9.6},
        ]})
    assert "多位同名老师" in amb
    # mixed 卡片带提示
    mcard = teachers.get_teacher_card(4, db_path=db)
    mhtml = teachers.render_card_html(mcard)
    assert "tc-caveat" in mhtml
    _cleanup(db)


# ---------- LLM 假件与降级 ----------


def _mk_llm(content: str):
    """构造返回固定 content 的假 llm（dispatch 经 chat.completions.create）。"""
    from types import SimpleNamespace

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=content))])

    class FakeChat:
        completions = FakeCompletions()

    class FakeLLM:
        chat = FakeChat()

    return FakeLLM()


def test_llm_extract_teacher_ok():
    llm = _mk_llm('{"is_teacher": true, "name": "洪鑫", "course": "微积分"}')
    out = teachers.llm_extract_teacher(llm, "洪鑫的微积分讲得怎么样")
    assert out and out["name"] == "洪鑫" and out["course"] == "微积分"


def test_llm_extract_teacher_bad_json_and_error():
    # 畸形 JSON → None
    llm = _mk_llm("不是 JSON")
    assert teachers.llm_extract_teacher(llm, "洪鑫怎么样") is None
    # 抛异常 → None
    class Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    raise RuntimeError("network down")
    assert teachers.llm_extract_teacher(Boom(), "洪鑫怎么样") is None


def test_maybe_card_pure_and_fallback():
    db = _fresh_db()
    # 单候选规则命中，无 LLM 也能出卡片
    card = teachers.maybe_card("洪鑫的计量经济学讲得怎么样", "洪鑫的计量经济学讲得怎么样", llm=None, db_path=db)
    assert card is not None and card["teacher"]["id"] == 1
    # 多候选 + 课程消歧 → 收窄到 1
    card2 = teachers.maybe_card("洪鑫的有机化学讲得怎么样", "洪鑫的有机化学讲得怎么样", llm=None, db_path=db)
    assert card2 is not None and card2["teacher"]["id"] == 2
    # 多候选无消歧信息 + 无 LLM → ambiguous
    card3 = teachers.maybe_card("洪鑫怎么样", "洪鑫怎么样", llm=None, db_path=db)
    assert card3["kind"] == "ambiguous"
    # 非老师问题 → None
    assert teachers.maybe_card("转专业需要什么条件", "转专业需要什么条件", llm=None, db_path=db) is None
    # LLM 兜底识别到未收录老师 → not_found
    llm = _mk_llm('{"is_teacher": true, "name": "陈晓华", "course": null}')
    card4 = teachers.maybe_card("陈晓华老师怎么样", "陈晓华老师怎么样", llm=llm, db_path=db)
    assert card4 is not None and card4["kind"] == "not_found"
    _cleanup(db)


def test_comment_quality():
    from core.teachers import JUNK_JUNK, JUNK_KEEP, JUNK_REVIEW, comment_quality

    # 纯垃圾
    assert comment_quality("几把没我的大") == JUNK_JUNK
    assert comment_quality("傻逼") == JUNK_JUNK
    assert comment_quality("废物") == JUNK_JUNK
    assert comment_quality("。。。") == JUNK_JUNK
    assert comment_quality("？？？") == JUNK_JUNK
    assert comment_quality("") == JUNK_JUNK
    assert comment_quality("脑子有坑吧") == JUNK_JUNK
    # 短评豁免：有效评价词保留
    assert comment_quality("好") == JUNK_KEEP
    assert comment_quality("棒") == JUNK_KEEP
    assert comment_quality("男神") == JUNK_KEEP
    # 有内容批评/反馈保留（即使含脏话）
    assert comment_quality("垃圾课，老师讲得稀烂") == JUNK_KEEP
    assert comment_quality("这老师就是个废物") == JUNK_KEEP
    assert comment_quality("卧槽老师太牛了") == JUNK_KEEP
    assert comment_quality("去你妈的这课也太难了") == JUNK_KEEP
    # 正常评价
    assert comment_quality("老师讲课很清晰，非常推荐") == JUNK_KEEP


def test_get_teacher_card_filters_junk():
    db = _fresh_db()
    # 插一条垃圾评论 + 一条清洗后评论
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO comments (id,teacher_id,published_at,net_votes,up,down,content,weight,junk,sanitized) "
        "VALUES (100,1,1700000000,90,100,10,'几把没我的大',90.0,1,NULL)"
    )
    conn.execute(
        "INSERT INTO comments (id,teacher_id,published_at,net_votes,up,down,content,weight,junk,sanitized) "
        "VALUES (101,1,1700000000,50,60,10,'老师对学生有恶意，根本不会讲课',50.0,0,'老师对学生态度不佳，讲课效果差')"
    )
    conn.commit()
    conn.close()
    card = teachers.get_teacher_card(1, db_path=db)
    contents = [c["content"] for cl in card["clusters"] for c in cl["comments"]]
    assert "几把没我的大" not in contents, "垃圾评论应被过滤"
    assert "老师对学生态度不佳，讲课效果差" in contents, "清洗后评论应展示"
    assert "老师对学生有恶意" not in contents, "原文不应展示（用清洗版）"
    _cleanup(db)


def test_render_card_html_newlines():
    """评论换行处理：字面 \n（反斜杠+n）还原为真实换行，连续换行压缩，转 <br> 显示。"""
    card = {
        "teacher": {"id": 1, "name": "测试", "college": "学院",
                    "rating": 9.0, "rating_count": 10, "hot": 5},
        "courses": [],
        "clusters": [{"id": 0, "caveat": False, "comments": [
            {"published_at": 1700000000, "net_votes": 5,
             "content": "第一行\\n\\n\\n\\n第二行\\n第三行"},
        ]}],
        "mixed": False,
    }
    html = teachers.render_card_html(card)
    # 4 个连续字面 \n → 压缩成 2 个 <br>（3 段文本）
    assert html.count("<br>") == 2
    # 不留字面 \n 或连续换行
    assert "\\n" not in html
    assert "\n\n\n" not in html
    # HTML 转义仍生效（<script> 不应原样输出）
    card2 = {
        "teacher": card["teacher"], "courses": [], "mixed": False,
        "clusters": [{"id": 0, "caveat": False, "comments": [
            {"published_at": 1700000000, "net_votes": 0, "content": "<script>x</script>"},
        ]}],
    }
    assert "<script>" not in teachers.render_card_html(card2)


def test_detect_course_query():
    db = _fresh_db()
    # 精确匹配
    cq = teachers.detect_course_query('我要选计量经济学这门课，哪个老师比较好', db)
    assert cq and cq['course'] == '计量经济学'
    assert any(c['name'] == '洪鑫' for c in cq['candidates'])
    # 前 4 字兜底（无精确课程名）
    cq2 = teachers.detect_course_query('线性代数怎么选老师', db)
    assert cq2 and '线性代数' in cq2['course']
    # 无选课意图词 -> None
    assert teachers.detect_course_query('数据结构基础是必修吗', db) is None
    # 无课程命中 -> None
    assert teachers.detect_course_query('我要选烹饪课，哪个老师好', db) is None
    _cleanup(db)


def test_maybe_card_course():
    db = _fresh_db()
    card = teachers.maybe_card('我要选计量经济学这门课，哪些老师比较好', '我要选计量经济学这门课，哪些老师比较好', llm=None, db_path=db)
    assert card is not None and card["kind"] == "course"
    assert card["course"] == "计量经济学"
    assert any(c["name"] == "洪鑫" for c in card["candidates"])
    # 排序：评分人数>0 的按评分降序，0 分垫底
    # _sort_course_candidates：评分人数>0 的按评分降序，0 分垫底
    cands = [
        {"rating": 2.5, "rating_count": 646, "name": "陈越"},
        {"rating": 9.8, "rating_count": 93, "name": "朱建科"},
        {"rating": 0.0, "rating_count": 0, "name": "某老师"},
    ]
    ordered = teachers._sort_course_candidates(cands)
    assert [c["name"] for c in ordered] == ["朱建科", "陈越", "某老师"]
    _cleanup(db)


def test_render_card_html_course():
    card = {
        "kind": "course", "course": "数据结构基础",
        "candidates": [
            {"name": "朱建科", "college": "计算机科学与技术学院", "rating": 9.8, "rating_count": 93, "courses": ["数据结构基础"]},
            {"name": "陈越", "college": "计算机科学与技术学院", "rating": 2.5, "rating_count": 646, "courses": ["数据结构基础"]},
            {"name": "某老师", "college": "未知", "rating": 0.0, "rating_count": 0, "courses": []},
            {"name": "新老师", "college": "未知", "rating": 8.0, "rating_count": 3, "courses": []},
        ],
    }
    h = teachers.render_card_html(card)
    assert "选课推荐" in h
    assert "数据结构基础" in h
    assert "朱建科" in h and "陈越" in h
    assert "9.8/10（93人）" in h
    assert "暂无评分" in h
    assert "样本少" in h


def test_expand_course_slang():
    import json, tempfile
    db = _fresh_db()
    # 临时统一黑话文件（type=course）：fds->计量经济学（fixture 库里有），ds 是 ads 子串验证最长优先
    fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "fds": {"type": "course", "value": ["计量经济学"]},
            "ads": {"type": "course", "value": ["有机化学"]},
            "ds": {"type": "course", "value": ["微观经济学"]},
            "保研": {"type": "rag", "value": "推荐免试"},  # RAG 条目对课程展开不可见
        }, f, ensure_ascii=False)
    old = teachers.SLANG_FILE
    teachers.SLANG_FILE = path
    try:
        # 一对多 + 命中
        out = teachers._expand_course_slang("fds选哪个老师", db)
        assert out == ["计量经济学"], out
        # 最长词优先：ads 命中不展开其中的 ds
        out2 = teachers._expand_course_slang("ads选老师", db)
        assert out2 == ["有机化学"], out2
        # 未命中黑话 -> None
        assert teachers._expand_course_slang("转专业需要什么条件", db) is None
        # 单课程命中
        out3 = teachers._expand_course_slang("ds哪个老师好", db)
        assert out3 == ["微观经济学"], out3
    finally:
        teachers.SLANG_FILE = old
        os.unlink(path)
    _cleanup(db)

if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
