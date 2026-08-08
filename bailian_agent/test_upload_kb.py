from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bailian_agent.upload_kb import append_state, build_index_request, cloud_file_name, load_latest_manifest


class UploadKbTests(unittest.TestCase):
    def test_smart_chunk_size_is_1200(self) -> None:
        request = build_index_request("index-1", [{"cloud_file_id": "file-1"}])
        self.assertEqual(request.chunk_size, 1200)
        self.assertIsNone(request.chunk_mode)

    def test_cloud_file_name_is_unique_and_within_limit(self) -> None:
        base = {"relative_path": "政策/" + "很长的标题" * 30 + ".md"}
        first = cloud_file_name({**base, "doc_id": "kb-1234567890abcdef"})
        second = cloud_file_name({**base, "doc_id": "kb-fedcba0987654321"})
        self.assertLessEqual(len(first), 128)
        self.assertTrue(first.endswith("__1234567890.md"))
        self.assertNotEqual(first, second)

    def test_manifest_uses_latest_appended_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            append_state(path, {"identity_key": "doc:1", "status": "pending"})
            append_state(path, {"identity_key": "doc:1", "status": "uploaded"})
            self.assertEqual(load_latest_manifest(path)["doc:1"]["status"], "uploaded")

    def test_manifest_does_not_regress_uploaded_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.jsonl"
            append_state(path, {"identity_key": "doc:1", "status": "uploaded"})
            append_state(path, {"identity_key": "doc:1", "status": "parsing"})
            self.assertEqual(load_latest_manifest(path)["doc:1"]["status"], "uploaded")


if __name__ == "__main__":
    unittest.main()
