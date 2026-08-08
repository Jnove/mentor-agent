import unittest

import httpx

from bailian_agent.client import BailianClient, BailianError


class BailianClientTest(unittest.TestCase):
    def test_chat_parses_answer_session_and_references(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["Authorization"], "Bearer test-key")
            self.assertNotIn("session_id", request.read().decode())
            return httpx.Response(
                200,
                json={
                    "output": {
                        "text": "回答",
                        "session_id": "session-1",
                        "doc_references": [{"title": "政策文件", "text": "相关条款"}],
                    }
                },
            )

        client = BailianClient(
            "test-key",
            "app-1",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        result = client.chat("问题")
        self.assertEqual(result["text"], "回答")
        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["references"][0]["title"], "政策文件")

    def test_chat_sends_session_id(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn('"session_id":"session-1"', request.read().decode())
            return httpx.Response(200, json={"output": {"text": "追问回答"}})

        client = BailianClient(
            "test-key",
            "app-1",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        self.assertEqual(client.chat("追问", "session-1")["text"], "追问回答")

    def test_chat_reports_api_error(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"code": "BadRequest", "message": "参数错误"})
        )
        client = BailianClient("test-key", "app-1", http_client=httpx.Client(transport=transport))
        with self.assertRaisesRegex(BailianError, "参数错误"):
            client.chat("问题")


if __name__ == "__main__":
    unittest.main()
