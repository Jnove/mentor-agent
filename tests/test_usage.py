"""core.usage 纯函数测试（不依赖模型/网络，用临时 db）。

用法: python tests/test_usage.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import usage


def _fresh_db() -> str:
    return str(Path(tempfile.mkdtemp()) / "usage.db")

def test_log_and_summary():
    db = _fresh_db()
    usage.init_db(db)
    now = 1700000000
    q1 = usage.log_question(1, "转专业要什么条件", kb_hits=3, top_score=0.8,
                            bst=False, covered=True, now=now, db_path=db)
    assert q1 == 1
    usage.log_question(1, "竺院是干嘛的", kb_hits=0, top_score=0.0,
                       bst=True, covered=False, now=now, db_path=db)
    usage.log_question(2, "奖学金怎么申请", kb_hits=2, top_score=0.6,
                       bst=False, covered=True, now=now, db_path=db)
    s = usage.stats_summary(now=now, db_path=db)
    assert s["today_total"] == 3
    assert s["today_uncovered"] == 1

def test_daily_counts():
    db = _fresh_db()
    usage.init_db(db)
    base = 1700000000
    day = 86400
    usage.log_question(1, "a", covered=True, now=base, db_path=db)
    usage.log_question(1, "b", covered=True, now=base, db_path=db)
    usage.log_question(2, "c", covered=False, now=base + day, db_path=db)
    rows = usage.daily_counts(2, now=base + day, db_path=db)
    assert len(rows) == 2
    assert rows[0]["total"] == 2 and rows[0]["dau"] == 1
    assert rows[1]["total"] == 1 and rows[1]["uncovered"] == 1 and rows[1]["dau"] == 1

def test_feedback_and_resolved():
    db = _fresh_db()
    usage.init_db(db)
    qid = usage.log_question(1, "某问题", covered=True, db_path=db)
    assert usage.uncovered_rows(db_path=db) == []
    usage.set_feedback(qid, usage.FEEDBACK_DOWN, db_path=db)
    assert len(usage.uncovered_rows(db_path=db)) == 1
    usage.set_resolved(qid, True, db_path=db)
    assert usage.uncovered_rows(db_path=db) == []
    assert len(usage.uncovered_rows(include_resolved=True, db_path=db)) == 1

def test_uncovered_filter():
    db = _fresh_db()
    usage.init_db(db)
    qout = usage.log_question(1, "库外", covered=False, db_path=db)
    usage.log_question(2, "有命中", covered=True, db_path=db)
    assert [r["id"] for r in usage.uncovered_rows(db_path=db)] == [qout]

def _run_all() -> None:
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")


if __name__ == "__main__":
    _run_all()
