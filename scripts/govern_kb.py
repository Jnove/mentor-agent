"""审计并迁移 knowledge_base 到 KB_FORMAT.md schema v2。

默认只检查；加 --apply 才写回。--remove-duplicates 仅删除内容完全一致的副本。
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
from core.kb_schema import is_root_url, normalize_metadata, validate_metadata


def _files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.md") if path.is_file())


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


def migrate_file(path: Path, root: Path, *, apply: bool) -> tuple[dict, bool]:
    post = frontmatter.loads(path.read_text(encoding="utf-8-sig"))
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
    return normalized, changed


def audit(root: Path, *, apply: bool, remove_duplicates: bool) -> dict:
    root = root.resolve()
    paths = _files(root)
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

    paths = _files(root)
    source_types: Counter[str] = Counter()
    review_statuses: Counter[str] = Counter()
    errors: dict[str, list[str]] = {}
    warnings: dict[str, list[str]] = {}
    changed_files: list[str] = []
    root_urls: list[str] = []
    unknown_dates: list[str] = []

    for path in paths:
        rel = _rel(path, root)
        try:
            meta, changed = migrate_file(path, root, apply=apply)
        except Exception as exc:
            errors[rel] = [f"解析失败：{exc}"]
            continue
        if changed:
            changed_files.append(rel)
        result = validate_metadata(meta)
        if result.errors:
            errors[rel] = result.errors
        if result.warnings:
            warnings[rel] = result.warnings
        source_types[str(meta.get("source_type"))] += 1
        review_statuses[str(meta.get("review_status"))] += 1
        if is_root_url(meta.get("source_url")):
            root_urls.append(rel)
        if meta.get("publish_date") == "unknown":
            unknown_dates.append(rel)

    return {
        "schema_version": 2,
        "root": str(root),
        "documents": len(paths),
        "would_change_or_changed": len(changed_files),
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
    args = parser.parse_args()
    if args.remove_duplicates and not args.apply:
        parser.error("--remove-duplicates 必须与 --apply 一起使用")

    report = audit(args.root, apply=args.apply, remove_duplicates=args.remove_duplicates)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(f"documents={report['documents']}")
    print(f"changed={report['would_change_or_changed']}")
    print(f"removed_duplicates={len(report['removed_exact_duplicates'])}")
    print(f"errors={len(report['errors'])} warnings={len(report['warnings'])}")
    print(f"source_types={report['source_types']}")
    print(f"review_statuses={report['review_statuses']}")
    if report["root_url_files"]:
        print(f"root_url_needs_review={len(report['root_url_files'])}")
    if report["unknown_publish_date_files"]:
        print(f"unknown_publish_date={len(report['unknown_publish_date_files'])}")
    if report["errors"]:
        for path, messages in report["errors"].items():
            print(f"[ERROR] {path}: {'; '.join(messages)}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
