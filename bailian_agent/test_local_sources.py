from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bailian_agent.local_sources import attach_original_urls


class LocalSourcesTests(unittest.TestCase):
    def test_resolves_cloud_document_to_manifest_source_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            knowledge_base = project / "knowledge_base"
            manifest_dir = project / "bailian_agent"
            knowledge_base.mkdir()
            manifest_dir.mkdir()
            record = {
                "doc_id": "kb-local-1",
                "cloud_file_id": "file-cloud-1",
                "cloud_document_id": "file-cloud-1",
                "cloud_file_name": "文章__local1.md",
                "relative_path": "政策/文章.md",
                "source_url": "https://example.edu/original",
            }
            (manifest_dir / "upload_manifest.jsonl").write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            references = attach_original_urls(
                [{"doc_id": "file-cloud-1", "doc_name": "wrong-download.md", "title": "重名文章"}],
                knowledge_base,
            )

            self.assertEqual(references[0]["source_url"], "https://example.edu/original")

    def test_resolves_extensionless_cloud_file_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            knowledge_base = project / "knowledge_base"
            manifest_dir = project / "bailian_agent"
            knowledge_base.mkdir()
            manifest_dir.mkdir()
            record = {
                "cloud_file_name": "文章__local1.md",
                "source_url": "https://example.edu/original",
            }
            (manifest_dir / "upload_manifest.jsonl").write_text(
                json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            references = attach_original_urls(
                [{"doc_id": "", "doc_name": "文章__local1", "title": "文章"}],
                knowledge_base,
            )

            self.assertEqual(references[0]["source_url"], "https://example.edu/original")

    def test_resolves_linked_attachment_to_parent_original_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            knowledge_base = Path(directory) / "knowledge_base"
            knowledge_base.mkdir()
            (knowledge_base / "parent.md").write_text(
                "# 项目介绍\n\n[课程清单](BMI%20dual-degree%20courses.pdf)\n\n"
                "原文网址：https://example.edu/original\n",
                encoding="utf-8",
            )

            references = attach_original_urls(
                [{"doc_id": "", "doc_name": "BMI dual-degree courses", "title": ""}],
                knowledge_base,
            )

            self.assertEqual(references[0]["source_url"], "https://example.edu/original")


if __name__ == "__main__":
    unittest.main()
