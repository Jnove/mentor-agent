"""知识库 schema v2：字段归一化、保守推断和入库校验。"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse


SCHEMA_VERSION = 2
CATEGORIES = {"政策", "通知", "FAQ"}
SOURCE_TYPES = {
    "official_policy",
    "official_notice",
    "official_guide",
    "student_guide",
    "third_party",
    "unknown",
}
AUTHORITY_LEVELS = {"university", "department", "college", "student", "external", "unknown"}
REVIEW_STATUSES = {"needs_review", "verified", "rejected"}

REQUIRED_FIELDS = (
    "schema_version",
    "doc_id",
    "title",
    "source_url",
    "source_org",
    "source_type",
    "authority_level",
    "publish_date",
    "category",
    "tags",
    "valid",
    "review_status",
    "last_checked_at",
    "maintainer",
    "applies_to",
    "campuses",
    "colleges",
    "effective_from",
    "effective_until",
    "supersedes",
    "superseded_by",
)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DOC_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{7,63}$")
_CAMPUS_NAMES = ("紫金港", "玉泉", "西溪", "华家池", "之江", "舟山", "海宁")


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def make_doc_id(title: object, source_url: object) -> str:
    """用来源和标题生成稳定 ID；重复资料会得到同一个 ID。"""
    seed = f"{str(source_url).strip()}\n{str(title).strip()}".encode("utf-8")
    return "kb-" + hashlib.sha256(seed).hexdigest()[:16]


def _date_text(value: object, *, unknown_allowed: bool = False) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value).strip()
    if unknown_allowed and text.lower() == "unknown":
        return "unknown"
    return text


def _as_list(value: object, default: list[str] | None = None) -> list[str]:
    if value is None or value == "":
        return list(default or [])
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def is_placeholder_url(url: object) -> bool:
    text = str(url).lower()
    return any(token in text for token in ("example.", ".example", "xxx."))


def is_root_url(url: object) -> bool:
    parsed = urlparse(str(url))
    return bool(parsed.scheme and parsed.netloc and parsed.path in ("", "/") and not parsed.query)


def infer_source_type(meta: dict) -> str:
    url = str(meta.get("source_url", "")).lower()
    org = str(meta.get("source_org", ""))
    tags = " ".join(_as_list(meta.get("tags")))
    category = str(meta.get("category", ""))
    if is_placeholder_url(url):
        return "unknown"
    if "github.com" in url or "非官方资料" in tags or "github" in org.lower():
        return "student_guide"
    if category == "政策":
        return "official_policy"
    if category == "通知":
        return "official_notice"
    if category == "FAQ":
        return "official_guide"
    return "unknown"


def infer_authority_level(meta: dict, source_type: str) -> str:
    org = str(meta.get("source_org", ""))
    if source_type == "student_guide":
        return "student"
    if source_type in {"third_party", "unknown"}:
        return "unknown"
    if org.strip() == "浙江大学":
        return "university"
    if any(name in org for name in ("本科生院", "研究生院", "教务处", "学工部", "信息技术中心", "图书馆")):
        return "department"
    if "学院" in org or org.endswith("系") or "学园" in org:
        return "college"
    return "department"


def infer_applies_to(meta: dict, content: str) -> list[str]:
    haystack = " ".join((str(meta.get("title", "")), " ".join(_as_list(meta.get("tags"))), content[:2000]))
    audiences: list[str] = []
    if "本科新生" in haystack:
        audiences.append("本科新生")
    elif "本科" in haystack:
        audiences.append("本科生")
    if "研究生" in haystack:
        audiences.append("研究生")
    if "港澳台" in haystack:
        audiences.append("港澳台学生")
    return audiences or ["未明确"]


def infer_campuses(meta: dict, content: str) -> list[str]:
    haystack = " ".join((str(meta.get("title", "")), " ".join(_as_list(meta.get("tags"))), content[:1000]))
    found = [name + ("国际校区" if name == "海宁" else "校区") for name in _CAMPUS_NAMES if name in haystack]
    return found or ["未明确"]


def normalize_metadata(meta: dict, rel_path: str | Path, content: str) -> dict:
    """把 v1/非规范元数据迁移为 v2；未知事实保留为待核验值。"""
    old = dict(meta)
    source_type = str(old.get("source_type") or infer_source_type(old))
    authority = str(old.get("authority_level") or infer_authority_level(old, source_type))
    valid = old.get("valid", True)
    valid = valid if isinstance(valid, bool) else str(valid).strip().lower() == "true"
    review_status = str(old.get("review_status") or ("rejected" if not valid else "needs_review"))
    title = str(old.get("title", "")).strip()
    source_url = str(old.get("source_url", "")).strip()
    source_org = str(old.get("source_org", "")).strip()
    publish_date = _date_text(old.get("publish_date"), unknown_allowed=True)
    if publish_date != "unknown" and (publish_date is None or not _DATE.fullmatch(publish_date)):
        publish_date = "unknown"
    colleges = _as_list(old.get("colleges"), ["未明确"])
    # v1 自动迁移曾把发布学院当成适用学院；在尚未人工核验时撤销这种推断。
    if (
        authority == "college"
        and review_status == "needs_review"
        and not _date_text(old.get("last_checked_at"))
        and str(old.get("maintainer") or "unassigned") == "unassigned"
        and colleges == [source_org]
    ):
        colleges = ["未明确"]

    if is_placeholder_url(source_url):
        valid = False
        review_status = "rejected"

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "doc_id": str(old.get("doc_id") or make_doc_id(title, source_url)),
        "title": title,
        "source_url": source_url,
        "source_org": source_org,
        "source_type": source_type,
        "authority_level": authority,
        "publish_date": publish_date,
        "category": str(old.get("category", "")).strip(),
        "tags": _as_list(old.get("tags")),
        "valid": valid,
        "review_status": review_status,
        "last_checked_at": _date_text(old.get("last_checked_at")),
        "maintainer": str(old.get("maintainer") or "unassigned").strip(),
        "applies_to": _as_list(old.get("applies_to"), infer_applies_to(old, content)),
        "campuses": _as_list(old.get("campuses"), infer_campuses(old, content)),
        "colleges": colleges,

        "effective_from": _date_text(old.get("effective_from")),
        "effective_until": _date_text(old.get("effective_until")),
        "supersedes": _as_list(old.get("supersedes")),
        "superseded_by": _as_list(old.get("superseded_by")),
    }
    for key, value in old.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def validate_metadata(meta: dict) -> ValidationResult:
    result = ValidationResult()
    for key in REQUIRED_FIELDS:
        if key not in meta:
            result.errors.append(f"缺少字段 {key}")

    if result.errors:
        return result
    if meta.get("schema_version") != SCHEMA_VERSION:
        result.errors.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    if not _DOC_ID.fullmatch(str(meta.get("doc_id", ""))):
        result.errors.append("doc_id 必须是 8~64 位小写字母、数字、下划线或连字符")
    for key in ("title", "source_org", "maintainer"):
        if not str(meta.get(key, "")).strip():
            result.errors.append(f"{key} 不能为空")

    url = str(meta.get("source_url", ""))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        result.errors.append("source_url 必须是完整的 http(s) 链接")
    if is_placeholder_url(url) and meta.get("valid") is not False:
        result.errors.append("占位来源必须 valid: false")
    if is_root_url(url):
        result.warnings.append("source_url 只指向网站首页，需补具体原文")

    if meta.get("category") not in CATEGORIES:
        result.errors.append(f"category 必须是 {sorted(CATEGORIES)} 之一")
    if meta.get("source_type") not in SOURCE_TYPES:
        result.errors.append(f"source_type 非法：{meta.get('source_type')}")
    if meta.get("authority_level") not in AUTHORITY_LEVELS:
        result.errors.append(f"authority_level 非法：{meta.get('authority_level')}")
    if meta.get("review_status") not in REVIEW_STATUSES:
        result.errors.append(f"review_status 非法：{meta.get('review_status')}")
    if not isinstance(meta.get("valid"), bool):
        result.errors.append("valid 必须是布尔值")
    if meta.get("review_status") == "rejected" and meta.get("valid") is not False:
        result.errors.append("rejected 文档必须 valid: false")

    publish_date = _date_text(meta.get("publish_date"), unknown_allowed=True)
    if publish_date != "unknown" and (publish_date is None or not _DATE.fullmatch(publish_date)):
        result.errors.append("publish_date 必须是 YYYY-MM-DD 或 unknown")
    for key in ("last_checked_at", "effective_from", "effective_until"):
        value = _date_text(meta.get(key))
        if value is not None and not _DATE.fullmatch(value):
            result.errors.append(f"{key} 必须是 YYYY-MM-DD 或 null")

    for key in ("tags", "applies_to", "campuses", "colleges", "supersedes", "superseded_by"):
        if not isinstance(meta.get(key), list):
            result.errors.append(f"{key} 必须是 YAML 列表")
    for key in ("tags", "applies_to", "campuses", "colleges"):
        if isinstance(meta.get(key), list) and not meta[key]:
            result.errors.append(f"{key} 不能为空列表")

    if meta.get("review_status") == "verified":
        if not _date_text(meta.get("last_checked_at")):
            result.errors.append("verified 文档必须填写 last_checked_at")
        if str(meta.get("maintainer")) == "unassigned":
            result.errors.append("verified 文档必须指定 maintainer")
    if meta.get("source_type") == "student_guide" and meta.get("authority_level") != "student":
        result.warnings.append("student_guide 通常应使用 authority_level: student")

    return result
