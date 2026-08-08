"""Resolve Bailian document references against the local zw knowledge base."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path


_ORIGINAL_URL = re.compile(r"(?m)^原文网址[：:]\s*(https?://\S+)\s*$")
_HEADING = re.compile(r"(?m)^#\s+(.+?)\s*$")


def _keys(value: str) -> tuple[str, ...]:
    name = Path(value.replace("\\", "/")).name.casefold()
    stem = Path(name).stem
    return tuple(dict.fromkeys((name, stem)))


@lru_cache(maxsize=1)
def _source_index(root: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in Path(root).rglob("*.md"):
        text = path.read_text(encoding="utf-8-sig")
        url_match = _ORIGINAL_URL.search(text)
        if not url_match:
            continue
        title_match = _HEADING.search(text)
        values = [path.name, path.stem]
        if title_match:
            values.append(title_match.group(1).strip())
        for value in values:
            for key in _keys(value):
                index.setdefault(key, url_match.group(1))
    return index


def attach_original_urls(references: list[dict], root: Path) -> list[dict]:
    """Replace temporary Bailian download URLs with local Markdown source URLs."""
    index = _source_index(str(root.resolve()))
    resolved = []
    for reference in references:
        source_url = ""
        for value in (reference.get("doc_name", ""), reference.get("title", "")):
            source_url = next((index[key] for key in _keys(value) if key in index), "")
            if source_url:
                break
        resolved.append({**reference, "source_url": source_url})
    return resolved
