"""审计并迁移 knowledge_base 到 KB_FORMAT.md schema v2。

默认只检查正式发布目录；加 --apply 才写回。--remove-duplicates 仅删除内容完全一致的副本。
dry-run 发现可发布文档待迁移时会返回非零，应用迁移后必须重新审计一次。
被隔离的 needs_review 待迁移项会报告，但不会阻断 verified 子集发布。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import frontmatter

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import KB_DIR
from core.kb_paths import count_ignored_markdown, iter_published_markdown
from core.kb_schema import (
    ValidationResult,
    is_publishable,
    is_root_url,
    normalize_metadata,
    validate_metadata,
)


def _files(root: Path, *, all_directories: bool = False) -> list[Path]:
    if all_directories:
        return sorted(path for path in root.rglob("*.md") if path.is_file())
    return iter_published_markdown(root)


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def find_exact_duplicates(paths: list[Path]) -> list[list[Path]]:
    groups: dict[str, list[Path]] = {}
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        groups.setdefault(digest, []).append(path)
    return [group for group in groups.values() if len(group) > 1]


def choose_canonical(group: list[Path], root: Path) -> Path:
    def rank(path: Path) -> tuple[int, int, str]:
        rel = _rel(path, root)
        if rel.startswith("政策/违纪处分/"):
            priority = 0
        elif rel.startswith("政策/学生管理/"):
            priority = 2
        else:
            priority = 1
        return priority, len(path.parts), rel

    return min(group, key=rank)


def migrate_file(path: Path, root: Path, *, apply: bool) -> tuple[dict, bool, ValidationResult]:
    post = frontmatter.loads(path.read_text(encoding="utf-8-sig"))
    original_result = validate_metadata(post.metadata)
    normalized = normalize_metadata(post.metadata, _rel(path, root), post.content)
    changed = normalized != post.metadata
    if apply and changed:
        post.metadata.clear()
        post.metadata.update(normalized)
        path.write_text(
            frontmatter.dumps(post, sort_keys=False).rstrip() + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return normalized, changed, original_result


def audit(root: Path, *, apply: bool, remove_duplicates: bool,
          all_directories: bool = False) -> dict:
    root = root.resolve()
    paths = _files(root, all_directories=all_directories)
    ignored_documents = 0 if all_directories else count_ignored_markdown(root)
    duplicate_groups = find_exact_duplicates(paths)
    removed: list[str] = []
    duplicate_report: list[dict] = []

    for group in duplicate_groups:
        keep = choose_canonical(group, root)
        copies = [path for path in group if path != keep]
        duplicate_report.append({
            "keep": _rel(keep, root),
            "copies": [_rel(path, root) for path in copies],
        })
        if apply and remove_duplicates:
            for path in copies:
                resolved = path.resolve()
                if root not in resolved.parents:
                    raise RuntimeError(f"拒绝删除知识库外文件：{resolved}")
                path.unlink()
                removed.append(_rel(path, root))

    paths = _files(root, all_directories=all_directories)
    source_types: Counter[str] = Counter()
    review_statuses: Counter[str] = Counter()
    errors: dict[str, list[str]] = {}
    warnings: dict[str, list[str]] = {}
    changed_files: list[str] = []
    publishable_changed_files: list[str] = []
    blocked_changed_files: list[str] = []
    root_urls: list[str] = []
    unknown_dates: list[str] = []
    publishable_documents = 0
    blocked_needs_review = 0

    for path in paths:
        rel = _rel(path, root)
        try:
            meta, changed, original_result = migrate_file(path, root, apply=apply)
        except Exception as exc:
            errors[rel] = [f"解析失败：{exc}"]
            continue
        result = validate_metadata(meta)
        current_errors = list(original_result.errors)
        current_errors.extend(error for error in result.errors if error not in current_errors)
        if current_errors:
            errors[rel] = current_errors
        if result.warnings:
            warnings[rel] = result.warnings
        source_types[str(meta.get("source_type"))] += 1
        review_statuses[str(meta.get("review_status"))] += 1
        if is_publishable(meta):
            publishable_documents += 1
            if changed:
                publishable_changed_files.append(rel)
        elif meta.get("valid") is True and meta.get("review_status") == "needs_review":
            blocked_needs_review += 1
            if changed:
                blocked_changed_files.append(rel)
        if changed:
            changed_files.append(rel)
        if is_root_url(meta.get("source_url")):
            root_urls.append(rel)
        if meta.get("publish_date") == "unknown":
            unknown_dates.append(rel)

    release_ready = bool(publishable_documents) and not all_directories and not (
        errors or publishable_changed_files or duplicate_groups
    )
    return {
        "schema_version": 2,
        "root": str(root),
        "documents": len(paths),
        "ignored_markdown_documents": ignored_documents,
        "publishable_documents": publishable_documents,
        "blocked_needs_review": blocked_needs_review,
        "release_ready": release_ready,
        "would_change_or_changed": len(changed_files),
        "publishable_would_change_or_changed": len(publishable_changed_files),
        "blocked_would_change_or_changed": len(blocked_changed_files),
        "removed_exact_duplicates": removed,
        "duplicate_groups": duplicate_report,
        "source_types": dict(sorted(source_types.items())),
        "review_statuses": dict(sorted(review_statuses.items())),
        "root_url_files": root_urls,
        "unknown_publish_date_files": unknown_dates,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=KB_DIR, help="知识库目录")
    parser.add_argument("--apply", action="store_true", help="写回 schema v2 元数据")
    parser.add_argument(
        "--remove-duplicates",
        action="store_true",
        help="删除内容完全一致的重复副本（必须与 --apply 一起使用）",
    )
    parser.add_argument("--report", type=Path, help="把完整报告写为 JSON")
    parser.add_argument(
        "--all-directories",
        action="store_true",
        help="迁移审计专用：包含 staging/raw；不能作为生产发布门禁",
    )
    args = parser.parse_args()
    if args.remove_duplicates and not args.apply:
        parser.error("--remove-duplicates 必须与 --apply 一起使用")

    report = audit(
        args.root,
        apply=args.apply,
        remove_duplicates=args.remove_duplicates,
        all_directories=args.all_directories,
    )
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"documents={report['documents']}")
    print(f"ignored_markdown={report['ignored_markdown_documents']}")
    print(f"publishable={report['publishable_documents']}")
    print(f"blocked_needs_review={report['blocked_needs_review']}")
    print(f"changed={report['would_change_or_changed']}")
    print(f"publishable_changed={report['publishable_would_change_or_changed']}")
    print(f"blocked_changed={report['blocked_would_change_or_changed']}")
    print(f"removed_duplicates={len(report['removed_exact_duplicates'])}")
    print(f"errors={len(report['errors'])} warnings={len(report['warnings'])}")
    print(f"source_types={report['source_types']}")
    print(f"review_statuses={report['review_statuses']}")
    print(f"release_ready={str(report['release_ready']).lower()}")
    if report["root_url_files"]:
        print(f"root_url_needs_review={len(report['root_url_files'])}")
    if report["unknown_publish_date_files"]:
        print(f"unknown_publish_date={len(report['unknown_publish_date_files'])}")
    if report["errors"]:
        for path, messages in report["errors"].items():
            print(f"[ERROR] {path}: {'; '.join(messages)}")
    if (
        report["errors"]
        or report["publishable_would_change_or_changed"]
        or report["duplicate_groups"]
        or (not args.all_directories and not report["release_ready"])
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
