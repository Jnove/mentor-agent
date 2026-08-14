"""入库发布状态门禁测试。"""
import sys
import tempfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("chromadb", SimpleNamespace())

import ingest


def _document(status: str = "verified", *, valid: bool = True) -> str:
    checked = '"2026-08-10"' if status == "verified" else "null"
    maintainer = "ops-test" if status == "verified" else "unassigned"
    return f'''---
schema_version: 2
doc_id: kb-12345678
title: 测试政策
source_url: https://www.zju.edu.cn/test
source_org: 浙江大学
source_type: official_policy
authority_level: university
publish_date: "2026-08-01"
category: 政策
tags: [测试]
valid: {str(valid).lower()}
review_status: {status}
last_checked_at: {checked}
maintainer: {maintainer}
applies_to: [本科生]
campuses: [紫金港校区]
colleges: [未明确]
effective_from: null
effective_until: null
supersedes: []
superseded_by: []
---
正文
'''


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_load_docs_defaults_to_verified_only():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "政策/verified.md", _document())
        _write(root, "FAQ/review.md", _document("needs_review"))
        _write(root, "通知/rejected.md", _document("rejected", valid=False))
        _write(root, "zju-welcome/raw.md", "没有 front matter")
        _write(root, "staging/bad.md", "也没有 front matter")

        output = StringIO()
        with redirect_stdout(output):
            strict = ingest.load_docs(kb_dir=root)
        assert [path.name for path, _, _ in strict] == ["verified.md"]
        log = output.getvalue()
        assert "[跳过汇总] 失效或拒绝 1 篇；尚未人工核验 1 篇" in log
        assert "review.md" not in log and "rejected.md" not in log

        review = ingest.load_docs(include_needs_review=True, kb_dir=root)
        assert {path.name for path, _, _ in review} == {"verified.md", "review.md"}


def test_bad_schema_inside_published_directory_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "政策/bad.md", "没有 front matter")
        try:
            ingest.load_docs(kb_dir=root)
            assert False, "正式目录中的坏 schema 必须阻断入库"
        except SystemExit as exc:
            assert "1 篇文档" in str(exc)


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
