"""Resolve Bailian document references against the local knowledge base."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote


_ORIGINAL_URL = re.compile(r"(?m)^原文网址[：:]\s*(https?://\S+)\s*$")
_SOURCE_URL = re.compile(r"(?m)^source_url:\s*[\"']?(https?://[^\s\"']+)")
_HEADING = re.compile(r"(?m)^#\s+(.+?)\s*$")
_LINK_TARGET = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def _keys(value: str) -> tuple[str, ...]:
    normalized = value.replace("\\", "/").strip().casefold()
    if not normalized:
        return ()
    name = Path(normalized).name
    stem = Path(name).stem
    path_stem = str(Path(normalized).with_suffix(""))
    return tuple(dict.fromkeys((normalized, path_stem, name, stem)))


@lru_cache(maxsize=1)
def _source_index(root: str) -> dict[str, str]:
    index: dict[str, str] = {}
    root_path = Path(root)
    manifest_path = root_path.parent / "bailian_agent" / "upload_manifest.jsonl"
    if manifest_path.is_file():
        with manifest_path.open("r", encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                url = record.get("source_url", "")
                if not url:
                    continue
                for value in (
                    record.get("cloud_file_id", ""),
                    record.get("cloud_document_id", ""),
                    record.get("cloud_file_name", ""),
                    record.get("doc_id", ""),
                    record.get("relative_path", ""),
                ):
                    for key in _keys(value):
                        index.setdefault(key, url)
    for path in root_path.rglob("*.md"):
        try:
            text = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        url_match = _ORIGINAL_URL.search(text) or _SOURCE_URL.search(text)
        if not url_match:
            continue
        title_match = _HEADING.search(text)
        values = [path.relative_to(root_path).as_posix(), path.name, path.stem]
        if title_match:
            values.append(title_match.group(1).strip())
        for target in _LINK_TARGET.findall(text):
            target = unquote(target.strip().strip("<>").split("#", 1)[0])
            if target and not target.startswith(("http://", "https://")):
                values.append(target)
        for value in values:
            for key in _keys(value):
                index.setdefault(key, url_match.group(1))
    if "school_calendar" in index:
        index.setdefault("calendar", index["school_calendar"])
    return index


def attach_original_urls(references: list[dict], root: Path) -> list[dict]:
    """Replace temporary Bailian download URLs with local Markdown source URLs."""
    index = _source_index(str(root.resolve()))
    resolved = []
    for reference in references:
        source_url = ""
        for value in (
            reference.get("doc_id", ""),
            reference.get("doc_name", ""),
            reference.get("title", ""),
        ):
            source_url = next((index[key] for key in _keys(value) if key in index), "")
            if source_url:
                break
        resolved.append({**reference, "source_url": source_url})
    return resolved
