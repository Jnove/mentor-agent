import tempfile
import unittest
from pathlib import Path

import httpx

from bailian_agent.client import BailianClient, BailianError, load_exported_credentials


def event_stream(*events: dict) -> str:
    import json

    return "\n\n".join(f"data: {json.dumps(event, ensure_ascii=False)}" for event in events)


class BailianClientTest(unittest.TestCase):
    def test_chat_parses_answer_and_references(self):
        tool_event = {
            "output": {"choices": [{"message": {
                "role": "tool",
                "additional_kwargs": {"extra_json": {"docs": [{
                    "_citation_index": 3,
                    "title": "政策文件",
                    "content": "相关条款",
                    "doc_id": "doc-1",
                    "doc_url": "https://example.edu/policy",
                }]}},
                "extra": {"group": "planning"},
            }}]}
        }
        answer_event = {
            "output": {"choices": [{"message": {
                "role": "assistant",
                "content": "回答<ref>[3]</ref>",
                "additional_kwargs": {},
                "extra": {"group": "generating"},
            }}]}
        }

        def handler(request: httpx.Request) -> httpx.Response:
            body = request.read().decode()
            self.assertIn('"agent_id":"aid-1"', body)
            self.assertIn('"content":"问题"', body)
            return httpx.Response(200, text=event_stream(tool_event, answer_event))

        client = BailianClient(
            "test-key",
            "aid-1",
            "workspace.cn-beijing.maas.aliyuncs.com",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = client.chat([{"role": "user", "content": "问题"}])
        self.assertEqual(result["text"], "回答[3]")
        self.assertEqual(result["references"][0]["index"], 3)
        self.assertEqual(result["references"][0]["doc_id"], "doc-1")

    def test_chat_reports_api_error(self):
        error_event = {"code": "400", "message": "参数错误"}
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=event_stream(error_event))
        )
        client = BailianClient(
            "test-key",
            "aid-1",
            "workspace.cn-beijing.maas.aliyuncs.com",
            http_client=httpx.Client(transport=transport),
        )
        with self.assertRaisesRegex(BailianError, "参数错误"):
            client.chat([{"role": "user", "content": "问题"}])

    def test_load_exported_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.csv"
            path.write_text("id,value\napiKey,test-key\napiHost,example.com\n", encoding="utf-8")
            credentials = load_exported_credentials(path)
        self.assertEqual(credentials["apiKey"], "test-key")
        self.assertEqual(credentials["apiHost"], "example.com")


if __name__ == "__main__":
    unittest.main()
