"""core 纯函数测试（不依赖模型/网络）。

用法: python tests/test_core.py
"""
import os
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.chunking import split_by_headings
from core.config import MAX_CHUNK_CHARS
from core.llm import build_context, renumber_citations, stream_answer
from core.notes import dedup_sources, notes_to_markdown, snippet
from core.retrieval import load_reranker, pick_with_coverage, rrf_fuse, tokenize


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


def test_load_reranker_preserves_proxy_environment():
    """服务器可能只能经代理下载模型，加载器不能静默删除部署方配置。"""
    fake_module = ModuleType("sentence_transformers")
    observed = {}

    class FakeCrossEncoder:
        def __init__(self, model_name, max_length):
            observed.update(
                model_name=model_name,
                max_length=max_length,
                http_proxy=os.environ.get("HTTP_PROXY"),
                https_proxy=os.environ.get("HTTPS_PROXY"),
            )

    fake_module.CrossEncoder = FakeCrossEncoder
    proxy_env = {
        "RERANK_MODEL": "test/reranker",
        "HTTP_PROXY": "http://proxy.internal:8080",
        "HTTPS_PROXY": "http://proxy.internal:8080",
    }
    with patch.dict(os.environ, proxy_env, clear=False), patch.dict(
        sys.modules, {"sentence_transformers": fake_module}
    ):
        model = load_reranker()

    assert isinstance(model, FakeCrossEncoder)
    assert observed == {
        "model_name": "test/reranker",
        "max_length": 512,
        "http_proxy": proxy_env["HTTP_PROXY"],
        "https_proxy": proxy_env["HTTPS_PROXY"],
    }


def test_split_by_headings():
    doc = "开头\n\n## 一\n内容A\n\n## 二\n内容B\n\n### 二点一\n内容C"
    chunks = split_by_headings(doc)
    assert len(chunks) == 4, chunks
    assert chunks[1].startswith("## 一")
    # 超长块按段落再切
    long_doc = "## 长\n" + "\n\n".join("段" * 300 for _ in range(4))
    assert len(split_by_headings(long_doc)) > 1


def test_split_long_section_keeps_heading():
    # 超长小节切成多块后，首块带原标题，续块带「标题（续）」——不丢检索上下文
    doc = "## 长\n" + "\n\n".join(f"第{i}段" + "内" * 300 for i in range(3))
    chunks = split_by_headings(doc)
    assert len(chunks) == 3, chunks
    assert chunks[0].startswith("## 长\n第0段")
    assert all(c.startswith("## 长（续）\n") for c in chunks[1:]), chunks


def test_split_long_single_line_paragraph():
    # 中文散文段在 markdown 里常整段一行（无换行），要能按句切开
    doc = "## 段\n" + "这是政策说明的一句话。" * 80  # 880 字单行
    chunks = split_by_headings(doc)
    assert len(chunks) > 1, len(chunks)
    # 标题在预算内先扣掉了，成块后不应明显超出 MAX_CHUNK_CHARS
    assert all(len(c) <= MAX_CHUNK_CHARS + 20 for c in chunks), [len(c) for c in chunks]
    assert chunks[1].startswith("## 段（续）\n")


def test_split_single_line_section_not_dropped():
    # 整节只有一行超长文本（爬虫脏数据常见），内容不能被静默丢弃
    doc = "## 报销流程 " + "先去办事大厅提交材料。" * 40
    chunks = split_by_headings(doc)
    assert chunks and sum(len(c) for c in chunks) >= 400, chunks


def test_split_long_table():
    # 无空行的超长 markdown 表格（KB_FORMAT 要求的表格写法）按行硬切，
    # 续块补表头两行，否则续块里的列没有含义
    rows = "\n".join(f"| 8月{i}日 | 上午 | 训练内容第{i}项说明文字 |" for i in range(1, 40))
    doc = "## 日程\n| 日期 | 时段 | 内容 |\n| --- | --- | --- |\n" + rows
    chunks = split_by_headings(doc)
    assert len(chunks) > 1, len(chunks)
    for c in chunks[1:]:
        assert c.startswith("## 日程（续）\n| 日期 | 时段 | 内容 |\n| --- | --- | --- |"), c
    # 所有数据行都保留（表头/重叠行允许重复）
    body = "\n".join(chunks)
    for i in range(1, 40):
        assert f"8月{i}日" in body


def test_pick_with_coverage():
    def h(f):
        return {"file": f}

    # 枚举场景：top2 之外仍有高分的未覆盖文档 -> 各补最优一块
    ranked = [(0.99, h("a")), (0.98, h("a")), (0.97, h("b")),
              (0.96, h("c")), (0.95, h("c"))]
    picked = pick_with_coverage(ranked, top_k=2, min_score=0.5, max_extra=5)
    assert [p["file"] for p in picked] == ["a", "b", "c"], picked

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
    # 检索空手而归（低分全被过滤）：给显式"无据"信号，目录仍在
    prompt3, sources3 = build_context("问?", [], cat)
    assert "没有找到" in prompt3 and "【知识库目录】" in prompt3
    assert len(sources3) == 2  # 目录来源仍参与编号


def test_stream_answer_filters_gateway_tail():
    from types import SimpleNamespace as NS

    # 网关流式收尾块 choices 为空、角色引导块 content 为 None，都要在 core 层挡掉
    chunks = [
        NS(choices=[NS(delta=NS(content=None))]),
        NS(choices=[NS(delta=NS(content="你"))]),
        NS(choices=[NS(delta=NS(content="好"))]),
        NS(choices=[]),
    ]
    fake_llm = NS(chat=NS(completions=NS(create=lambda **kw: iter(chunks))))
    assert "".join(stream_answer(fake_llm, [], "问")) == "你好"


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
    # 三位数编号也要能重映射（编号空间跨资料+全库目录，早已过百）
    many = [{"title": f"S{i}"} for i in range(120)]
    text, cited = renumber_citations("依据[103]。", many)
    assert text == "依据[1]。" and cited == [(1, many[102])]


def test_expand_query():
    from core.slang import expand_query

    assert "竺可桢学院" in expand_query("竺院是干什么的")
    assert "校园卡" in expand_query("一卡通丢了怎么办")
    # 未命中原样返回；正式名词已在 query 里不重复追加
    assert expand_query("转专业需要什么条件") == "转专业需要什么条件"
    assert expand_query("竺可桢学院怎么样").count("竺可桢学院") == 1


def test_strip_citations():
    from core.llm import strip_citations

    assert strip_citations("要求见[2]，另见[13]。") == "要求见，另见。"
    assert strip_citations("2025年文件[2025]不受影响") == "2025年文件[2025]不受影响"


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
