"""Build a local, mergeable upload manifest without calling Bailian APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")
_DOC_ID = re.compile(r"(?m)^doc_id:\s*['\"]?([^\s'\"]+)['\"]?\s*$")
_SOURCE_URL = re.compile(r"(?m)^source_url:\s*['\"]?(https?://[^\s'\"]+)")
_PUBLISH_DATE = re.compile(r"(?m)^publish_date:\s*['\"]?([^\r\n'\"]+)['\"]?\s*$")
_REMOVED_FIELD = re.compile(r"(?m)^(title|tags|valid|review_status):")
_PRESERVED_STATUSES = {
    "uploaded",
    "parsing",
    "ready",
    "indexing",
    "upload_failed",
    "parse_failed",
    "index_failed",
}


def normalize_url(value: str) -> str:
    """Normalize only URL parts that cannot change the source identity."""
    parts = urlsplit(value.strip())
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def normalized_sha256(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _field(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    value = match.group(1).strip()
    return "" if value.lower() in {"null", "none"} else value


def _is_english_filename(path: Path) -> bool:
    return bool(_LATIN.search(path.stem)) and not _CJK.search(path.stem)


def _load_existing(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    records: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            identity = record.get("identity_key")
            if not identity:
                raise ValueError(f"manifest line {line_number} has no identity_key")
            if records.get(identity, {}).get("status") == "uploaded" and record.get("status") != "uploaded":
                continue
            records[identity] = record
    return records


def build_manifest(
    root: Path,
    manifest_path: Path,
    knowledge_base_id: str,
    *,
    prefer_primary_conflicts: bool = False,
) -> dict[str, int]:
    root = root.resolve()
    existing = _load_existing(manifest_path)
    groups: dict[str, list[dict]] = defaultdict(list)
    stats = {
        "markdown_files": 0,
        "excluded_english": 0,
        "excluded_bst": 0,
        "excluded_unprepared": 0,
        "candidate_files": 0,
        "manifest_entries": 0,
        "duplicate_files": 0,
        "conflicts": 0,
        "pending": 0,
        "uploaded": 0,
        "changed": 0,
        "missing_local": 0,
    }

    for path in sorted(root.rglob("*.md")):
        stats["markdown_files"] += 1
        relative_path = path.relative_to(root).as_posix()
        if _is_english_filename(path):
            stats["excluded_english"] += 1
            continue
        if "百事通" in relative_path:
            stats["excluded_bst"] += 1
            continue

        text = path.read_text(encoding="utf-8-sig")
        source_url = _field(_SOURCE_URL, text)
        doc_id = _field(_DOC_ID, text)
        prepared = not text.lstrip("\ufeff").startswith("---") and not _REMOVED_FIELD.search(text)
        if not prepared or not source_url or not doc_id:
            stats["excluded_unprepared"] += 1
            continue

        identity_key = "doc:" + doc_id
        groups[identity_key].append(
            {
                "doc_id": doc_id,
                "source_url": source_url,
                "normalized_source_url": normalize_url(source_url),
                "publish_date": _field(_PUBLISH_DATE, text),
                "relative_path": relative_path,
                "content_sha256": normalized_sha256(text),
                "is_staging": "staging" in path.relative_to(root).parts,
            }
        )
        stats["candidate_files"] += 1

    records: list[dict] = []
    for identity_key, documents in sorted(groups.items()):
        documents.sort(key=lambda item: (item["is_staging"], item["relative_path"].casefold()))
        primary = documents[0]
        duplicate_paths = [item["relative_path"] for item in documents[1:]]
        hashes = {item["content_sha256"] for item in documents}
        source_urls = {item["normalized_source_url"] for item in documents}
        conflict_reasons = []
        if len(hashes) > 1:
            conflict_reasons.append("content")
        if len(source_urls) > 1:
            conflict_reasons.append("source_url")
        conflict = bool(conflict_reasons)
        old = existing.get(identity_key, {})
        conflict_resolution = old.get("conflict_resolution")
        if conflict and prefer_primary_conflicts:
            conflict_resolution = "prefer_primary"
        same_content = old.get("content_sha256") == primary["content_sha256"]

        if conflict and conflict_resolution != "prefer_primary":
            status = "conflict"
            stats["conflicts"] += 1
        elif same_content and old.get("status") in _PRESERVED_STATUSES:
            status = old["status"]
        elif old.get("status") == "uploaded":
            status = "changed"
        else:
            status = "pending"

        record = {
            "identity_key": identity_key,
            "knowledge_base_id": knowledge_base_id,
            "doc_id": primary["doc_id"],
            "source_url": primary["source_url"],
            "publish_date": primary["publish_date"] or None,
            "relative_path": primary["relative_path"],
            "content_sha256": primary["content_sha256"],
            "status": status,
            "duplicate_paths": duplicate_paths,
            "conflict_reasons": conflict_reasons,
            "conflict_resolution": conflict_resolution,
            "cloud_document_id": old.get("cloud_document_id"),
            "cloud_file_id": old.get("cloud_file_id"),
            "cloud_file_name": old.get("cloud_file_name"),
            "index_job_id": old.get("index_job_id"),
            "parse_status": old.get("parse_status"),
            "last_error": old.get("last_error"),
            "attempts": old.get("attempts", 0),
            "uploaded_at": old.get("uploaded_at"),
        }
        records.append(record)
        if status in stats:
            stats[status] += 1
        stats["duplicate_files"] += len(duplicate_paths)

    current_identities = set(groups)
    for identity_key, old in sorted(existing.items()):
        if identity_key in current_identities or old.get("status") != "uploaded":
            continue
        record = {**old, "status": "missing_local"}
        records.append(record)
        stats["missing_local"] += 1

    records.sort(key=lambda item: item["identity_key"])
    stats["manifest_entries"] = len(records)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary_path.replace(manifest_path)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-root", type=Path, default=Path("knowledge_base"))
    parser.add_argument("--manifest", type=Path, default=Path("bailian_agent/upload_manifest.jsonl"))
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--prefer-primary-conflicts", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            build_manifest(
                args.knowledge_base_root,
                args.manifest,
                args.knowledge_base_id,
                prefer_primary_conflicts=args.prefer_primary_conflicts,
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
