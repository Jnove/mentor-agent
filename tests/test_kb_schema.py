"""知识库 schema v2 的离线测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.kb_schema import (
    is_publishable,
    make_doc_id,
    normalize_metadata,
    validate_metadata,
)


def old_meta(**overrides):
    meta = {
        "title": "本科生转专业管理办法",
        "source_url": "https://zdbk.zju.edu.cn/notice/1.htm",
        "source_org": "本科生院",
        "publish_date": "2025-09-01",
        "category": "政策",
        "tags": ["转专业", "本科生"],
        "valid": True,
    }
    meta.update(overrides)
    return meta


def test_normalize_old_metadata():
    meta = normalize_metadata(old_meta(), "政策/转专业.md", "面向本科学生。")
    assert meta["schema_version"] == 2
    assert meta["source_type"] == "official_policy"
    assert meta["authority_level"] == "department"
    assert meta["review_status"] == "needs_review"
    assert meta["maintainer"] == "unassigned"
    assert "本科生" in meta["applies_to"]
    assert validate_metadata(meta).ok


def test_student_guide_inference():
    meta = normalize_metadata(
        old_meta(
            source_url="https://github.com/example/guide/blob/abc/docs/a.md",
            source_org="学生编委会（GitHub）",
            category="FAQ",
            tags=["非官方资料", "本科新生"],
        ),
        "FAQ/a.md",
        "",
    )
    assert meta["source_type"] == "student_guide"
    assert meta["authority_level"] == "student"
    assert meta["applies_to"] == ["本科新生"]


def test_placeholder_is_rejected():
    meta = normalize_metadata(
        old_meta(source_url="https://jwc.example.edu.cn/1", valid=True),
        "政策/示例.md",
        "",
    )
    assert meta["valid"] is False
    assert meta["review_status"] == "rejected"
    assert validate_metadata(meta).ok


def test_verified_requires_owner_and_check_date():
    meta = normalize_metadata(old_meta(), "政策/a.md", "")
    meta["review_status"] = "verified"
    result = validate_metadata(meta)
    assert not result.ok
    assert any("last_checked_at" in error for error in result.errors)
    assert any("maintainer" in error for error in result.errors)
    meta["last_checked_at"] = "2026-08-03"
    meta["maintainer"] = "data-team-1"
    assert validate_metadata(meta).ok


def test_invalid_legacy_date_becomes_unknown():
    meta = normalize_metadata(
        old_meta(publish_date="关于项目的问答"),
        "FAQ/项目问答.md",
        "",
    )
    assert meta["publish_date"] == "unknown"
    assert validate_metadata(meta).ok

def test_publisher_college_is_not_assumed_to_be_applicable_college():
    meta = normalize_metadata(
        old_meta(source_org="计算机学院"),
        "政策/学院细则.md",
        "",
    )
    assert meta["authority_level"] == "college"
    assert meta["colleges"] == ["未明确"]

def test_doc_id_is_stable():
    assert make_doc_id("A", "https://u") == make_doc_id("A", "https://u")
    assert make_doc_id("A", "https://u") != make_doc_id("B", "https://u")


def test_publishable_requires_verified_and_valid():
    assert is_publishable({"valid": True, "review_status": "verified"})
    assert not is_publishable({"valid": True, "review_status": "needs_review"})
    assert is_publishable(
        {"valid": True, "review_status": "needs_review"},
        include_needs_review=True,
    )
    assert not is_publishable(
        {"valid": False, "review_status": "verified"},
        include_needs_review=True,
    )
    assert not is_publishable({"valid": True, "review_status": "rejected"})


if __name__ == "__main__":
    fns = [value for name, value in list(globals().items()) if name.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} 个测试全部通过")
