"""core 纯函数测试（不依赖模型/网络）。

用法: python tests/test_core.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.chunking import split_by_headings
from core.llm import build_context, renumber_citations
from core.notes import dedup_sources, notes_to_markdown, snippet
from core.retrieval import pick_with_coverage, rrf_fuse, tokenize


def test_rrf_fuse():
    # 两路都排第一的 id 应融合为第一
    fused = rrf_fuse([["a", "b", "c"], ["a", "c", "b"]])
    assert fused[0] == "a", fused
    # 只出现在一路的 id 也应保留
    fused = rrf_fuse([["a"], ["b"]])
    assert set(fused) == {"a", "b"}
    # 空输入
    assert rrf_fuse([[], []]) == []


def test_tokenize():
    tokens = tokenize("本科生转专业管理办法，2025年6月报名！")
    assert len(tokens) > 3
    assert all(t.strip() for t in tokens)
    # 标点不应出现在结果里
    assert "，" not in tokens and "！" not in tokens


def test_split_by_headings():
    doc = "开头\n\n## 一\n内容A\n\n## 二\n内容B\n\n### 二点一\n内容C"
    chunks = split_by_headings(doc)
    assert len(chunks) == 4, chunks
    assert chunks[1].startswith("## 一")
    # 超长块按段落再切
    long_doc = "## 长\n" + "\n\n".join("段" * 300 for _ in range(4))
    assert len(split_by_headings(long_doc)) > 1


def test_pick_with_coverage():
    def h(f):
        return {"file": f}

    # 枚举场景：top2 之外仍有高分的未覆盖文档 -> 各补最优一块
    ranked = [(0.99, h("a")), (0.98, h("a")), (0.97, h("b")),
              (0.96, h("c")), (0.95, h("c"))]
    picked = pick_with_coverage(ranked, top_k=2, min_score=0.5, max_extra=5)
    assert [p["file"] for p in picked] == ["a", "a", "b", "c"], picked

    # 细节场景：其他文档得分低于阈值 -> 不补位，行为同 top_k 截断
    ranked = [(0.99, h("a")), (0.98, h("a")), (0.001, h("b"))]
    picked = pick_with_coverage(ranked, top_k=2, min_score=0.5, max_extra=5)
    assert [p["file"] for p in picked] == ["a", "a"], picked

    # 补位数量受 max_extra 限制
    ranked = [(0.9, h(str(i))) for i in range(10)]
    picked = pick_with_coverage(ranked, top_k=2, min_score=0.5, max_extra=3)
    assert len(picked) == 5, picked


def test_build_context():
    hit = {"title": "A", "source_url": "u1", "source_org": "O", "publish_date": "d",
           "text": "正文", "file": "政策/a.md"}
    cat = [
        {"title": "A", "source_url": "u1", "source_org": "O", "publish_date": "d",
         "file": "政策/a.md"},
        {"title": "B", "source_url": "u2", "source_org": "O", "publish_date": "d",
         "file": "政策/b.md"},
    ]
    prompt, sources = build_context("问?", [hit, hit], cat)
    # 同一篇文档（资料出现两次 + 目录一次）只占一个编号
    assert len(sources) == 2 and sources[0]["title"] == "A" and sources[1]["title"] == "B"
    assert "【知识库目录】" in prompt and "[1]《A》" in prompt and "[2]《B》" in prompt
    assert "【问题】\n问?" in prompt
    # 无目录时不输出目录段
    prompt2, _ = build_context("问?", [hit], [])
    assert "【知识库目录】" not in prompt2


def test_renumber_citations():
    sources = [{"title": "A"}, {"title": "B"}, {"title": "C"}]
    # 跳号引用按首次出现顺序重映射为 1、2；重复引用共用同一新编号
    text, cited = renumber_citations("先说C[3]，再说A[1]，又提C[3]。", sources)
    assert text == "先说C[1]，再说A[2]，又提C[1]。", text
    assert [(n, s["title"]) for n, s in cited] == [(1, "C"), (2, "A")]
    # 越界编号（政策文号等）原样保留、不算引用
    text, cited = renumber_citations("规定见[9]和2025年文件", sources)
    assert text == "规定见[9]和2025年文件" and cited == []
    # 完全没有标注 -> 原文不变、空列表（调用方兜底）
    text, cited = renumber_citations("没有标注", sources)
    assert text == "没有标注" and cited == []


def test_snippet():
    s = snippet("《标题》\n## 小节\n正文 **加粗** 内容", n=20)
    assert "《标题》" not in s and "#" not in s and "*" not in s


def test_dedup_sources():
    hits = [
        {"title": "A", "source_url": "u1", "x": 1},
        {"title": "A", "source_url": "u1", "x": 2},
        {"title": "B", "source_url": "u2", "x": 3},
    ]
    out = dedup_sources(hits)
    assert len(out) == 2 and out[0]["x"] == 1


def test_notes_to_markdown():
    md = notes_to_markdown([{
        "q": "问题?",
        "points": ["要点一", "要点二"],
        "sources": [{"title": "T", "source_org": "O", "source_url": "http://u"}],
    }])
    assert "## 1. 问题?" in md and "- 要点一" in md and "[《T》（O）](http://u)" in md


if __name__ == "__main__":
    fns = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
