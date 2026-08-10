"""知识库治理发布报告测试。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.govern_kb import audit


def _document(status: str = "verified") -> str:
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
valid: true
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


def test_audit_uses_release_boundary_and_reports_review_queue():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "政策/verified.md", _document())
        _write(root, "FAQ/review.md", _document("needs_review"))
        _write(root, "staging/legacy.md", "坏格式")
        _write(root, "zju-welcome/raw.md", "坏格式")

        report = audit(root, apply=False, remove_duplicates=False)
        assert report["documents"] == 2
        assert report["ignored_markdown_documents"] == 2
        assert report["publishable_documents"] == 1
        assert report["blocked_needs_review"] == 1
        assert report["release_ready"] is True


def test_pending_migration_is_not_release_ready():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        legacy = _document().replace("schema_version: 2\n", "")
        _write(root, "政策/legacy.md", legacy)
        report = audit(root, apply=False, remove_duplicates=False)
        assert report["would_change_or_changed"] == 1
        assert report["release_ready"] is False


def test_pending_migration_in_review_queue_does_not_block_verified_subset():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(root, "政策/verified.md", _document())
        legacy_review = (
            _document("needs_review")
            .replace("source_org: 浙江大学", "source_org: 计算机学院")
            .replace("authority_level: university", "authority_level: college")
            .replace("colleges: [未明确]", "colleges: [计算机学院]")
        )
        _write(root, "FAQ/review.md", legacy_review)
        report = audit(root, apply=False, remove_duplicates=False)
        assert report["would_change_or_changed"] == 1
        assert report["publishable_would_change_or_changed"] == 0
        assert report["blocked_would_change_or_changed"] == 1
        assert report["release_ready"] is True


if __name__ == "__main__":
    tests = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
