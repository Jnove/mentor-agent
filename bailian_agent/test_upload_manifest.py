from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bailian_agent.upload_manifest import _load_existing, build_manifest


def document(doc_id: str, url: str, body: str = "正文") -> str:
    return f"# 标题\n\n{body}\n\ndoc_id: {doc_id}\nsource_url: {url}\npublish_date: '2026-01-01'\n"


class UploadManifestTests(unittest.TestCase):
    def test_existing_uploaded_state_does_not_regress(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.jsonl"
            rows = [
                {"identity_key": "doc:1", "status": "uploaded"},
                {"identity_key": "doc:1", "status": "parsing"},
            ]
            manifest.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            self.assertEqual(_load_existing(manifest)["doc:1"]["status"], "uploaded")

    def test_prefers_non_staging_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "knowledge_base"
            formal = root / "政策" / "文章.md"
            staging = root / "staging" / "来源" / "文章.md"
            formal.parent.mkdir(parents=True)
            staging.parent.mkdir(parents=True)
            text = document("kb-1", "https://example.edu/1")
            formal.write_text(text, encoding="utf-8-sig")
            staging.write_text(text, encoding="utf-8-sig")
            manifest = Path(directory) / "manifest.jsonl"

            stats = build_manifest(root, manifest, "kb-test")
            record = json.loads(manifest.read_text(encoding="utf-8").strip())

            self.assertEqual(stats["manifest_entries"], 1)
            self.assertEqual(stats["duplicate_files"], 1)
            self.assertEqual(record["relative_path"], "政策/文章.md")
            self.assertEqual(record["duplicate_paths"], ["staging/来源/文章.md"])

    def test_excludes_english_and_bst_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "knowledge_base"
            root.mkdir()
            (root / "english.md").write_text(document("kb-1", "https://example.edu/1"), encoding="utf-8-sig")
            bst = root / "FAQ" / "百事通" / "文章.md"
            bst.parent.mkdir(parents=True)
            bst.write_text(document("kb-2", "https://example.edu/2"), encoding="utf-8-sig")
            manifest = Path(directory) / "manifest.jsonl"

            stats = build_manifest(root, manifest, "kb-test")

            self.assertEqual(stats["excluded_english"], 1)
            self.assertEqual(stats["excluded_bst"], 1)
            self.assertEqual(stats["manifest_entries"], 0)

    def test_marks_different_duplicate_content_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "knowledge_base"
            first = root / "政策" / "文章甲.md"
            second = root / "staging" / "文章乙.md"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text(document("kb-1", "https://example.edu/1", "版本一"), encoding="utf-8-sig")
            second.write_text(document("kb-1", "https://example.edu/1", "版本二"), encoding="utf-8-sig")
            manifest = Path(directory) / "manifest.jsonl"

            stats = build_manifest(root, manifest, "kb-test")
            record = json.loads(manifest.read_text(encoding="utf-8").strip())

            self.assertEqual(stats["conflicts"], 1)
            self.assertEqual(record["status"], "conflict")
            self.assertEqual(record["conflict_reasons"], ["content"])


if __name__ == "__main__":
    unittest.main()
