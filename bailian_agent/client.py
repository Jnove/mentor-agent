"""Minimal client for Alibaba Cloud Model Studio Knowledge Chat."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import httpx


class BailianError(RuntimeError):
    """Raised when Model Studio rejects a request or returns invalid data."""


def renumber_references(answer: str, references: list[dict]) -> tuple[str, list[tuple[int, dict]]]:
    """Renumber citations using Bailian's explicit citation indexes."""
    by_index = {int(reference["index"]): reference for reference in references}
    mapping: dict[int, int] = {}
    cited: list[tuple[int, dict]] = []

    def replace(match: re.Match) -> str:
        old = int(match.group(1))
        if old not in by_index:
            return match.group(0)
        if old not in mapping:
            mapping[old] = len(mapping) + 1
            cited.append((mapping[old], by_index[old]))
        return f"[{mapping[old]}]"

    return re.sub(r"\[(\d{1,3})]", replace, answer), cited


def load_exported_credentials(path: str | Path) -> dict[str, str]:
    """Read the key-value CSV exported by the Model Studio console."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise BailianError("百炼凭据 CSV 为空")
    value_column = next((name for name in rows[0] if name != "id"), None)
    if not value_column:
        raise BailianError("百炼凭据 CSV 格式不正确")
    return {row["id"]: row.get(value_column, "") for row in rows if row.get("id")}


class BailianClient:
    def __init__(
        self,
        api_key: str,
        agent_id: str,
        api_host: str,
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.url = f"https://{api_host.removeprefix('https://').rstrip('/')}/api/v2/apps/knowledge/chat"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        self.http = http_client or httpx.Client(timeout=60)

    def chat(self, messages: list[dict]) -> dict:
        payload = {
            "input": {"messages": messages},
            "parameters": {"agent_options": {"agent_id": self.agent_id}},
            "stream": True,
        }
        answer_parts: list[str] = []
        references: list[dict] = []

        try:
            with self.http.stream("POST", self.url, headers=self.headers, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    event = json.loads(raw)
                    if event.get("code") and str(event["code"]) not in {"200", "Success"}:
                        raise BailianError(f"百炼返回错误：{event.get('message') or event['code']}")
                    choices = (event.get("output") or {}).get("choices") or []
                    if not choices:
                        continue
                    message = choices[0].get("message") or {}
                    extra = message.get("extra") or {}
                    if extra.get("group") == "generating" and isinstance(message.get("content"), str):
                        answer_parts.append(message["content"])
                    docs = ((message.get("additional_kwargs") or {}).get("extra_json") or {}).get("docs") or []
                    if docs:
                        references = [self._reference(doc, index) for index, doc in enumerate(docs, 1)]
        except BailianError:
            raise
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise BailianError(f"百炼请求失败：{exc}") from exc

        text = "".join(answer_parts)
        if not text:
            raise BailianError("百炼没有返回回答文本")
        text = re.sub(r"<ref>\[(\d+)]</ref>", r"[\1]", text)
        return {"text": text, "references": references}

    @staticmethod
    def _reference(doc: dict, fallback_index: int) -> dict:
        return {
            "index": doc.get("_citation_index") or fallback_index,
            "title": doc.get("title") or doc.get("doc_name") or "未命名文档",
            "text": doc.get("content") or "",
            "doc_id": doc.get("doc_id") or "",
            "doc_name": doc.get("doc_name") or "",
            "doc_url": doc.get("doc_url") or "",
            "page_number": doc.get("page_number"),
        }
