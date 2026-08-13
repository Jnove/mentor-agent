"""知识库发布目录边界。

正式发布采用顶层目录 allowlist。暂存区、原始导入目录以及未来新增的未知目录默认拒绝，
避免一次递归扫描把未经审核或重复的 Markdown 静默放入生产索引。
"""
from __future__ import annotations

from pathlib import Path


PUBLISHED_TOP_LEVEL_DIRS = ("FAQ", "政策", "通知")


def iter_published_markdown(root: Path) -> list[Path]:
    """返回正式目录下的 Markdown，按相对路径稳定排序。"""
    root = root.resolve()
    paths: list[Path] = []
    for dirname in PUBLISHED_TOP_LEVEL_DIRS:
        directory = root / dirname
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*.md") if path.is_file())
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def count_ignored_markdown(root: Path) -> int:
    """统计 allowlist 之外的 Markdown 数量，用于审计报告。"""
    root = root.resolve()
    published = set(iter_published_markdown(root))
    return sum(
        1 for path in root.rglob("*.md")
        if path.is_file() and path.resolve() not in published
    )
