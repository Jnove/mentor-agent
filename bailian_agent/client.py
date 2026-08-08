"""Small HTTP client for an Alibaba Cloud Model Studio application."""

from __future__ import annotations

import httpx


class BailianError(RuntimeError):
    """Raised when Model Studio rejects a request or returns invalid data."""


class BailianClient:
    def __init__(
        self,
        api_key: str,
        app_id: str,
        *,
        base_url: str = "https://dashscope.aliyuncs.com/api/v1",
        workspace_id: str = "",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.app_id = app_id
        self.url = f"{base_url.rstrip('/')}/apps/{app_id}/completion"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if workspace_id:
            self.headers["X-DashScope-WorkSpace"] = workspace_id
        self.http = http_client or httpx.Client(timeout=60)

    def chat(self, prompt: str, session_id: str | None = None) -> dict:
        request_input = {"prompt": prompt}
        if session_id:
            request_input["session_id"] = session_id

        try:
            response = self.http.post(
                self.url,
                headers=self.headers,
                json={"input": request_input, "parameters": {}},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BailianError(f"百炼请求失败：{exc}") from exc

        if data.get("code") and str(data["code"]) not in {"200", "Success"}:
            raise BailianError(f"百炼返回错误：{data.get('message') or data['code']}")

        output = data.get("output") or {}
        text = output.get("text")
        if not text:
            raise BailianError("百炼没有返回回答文本")

        return {
            "text": text,
            "session_id": output.get("session_id"),
            "references": output.get("doc_references") or [],
        }
