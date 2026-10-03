from __future__ import annotations

import json
from collections.abc import Callable

from pulse.http_clients import outbound_client


class AssistantLlmClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, *, system: str, user: str, temperature: float = 0.1) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with outbound_client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"].strip()

    def complete_with_tools(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: float = 0.1,
        on_content_delta: Callable[[str], None] | None = None,
    ) -> dict:
        """``on_content_delta`` receives the accumulated content so far (streaming mode)."""
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if on_content_delta is not None:
            message = self._stream_message(url, headers, {**payload, "stream": True}, on_content_delta)
        else:
            with outbound_client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
            message = data["choices"][0]["message"]
        return _parse_assistant_message(message)

    def _stream_message(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict,
        on_content_delta: Callable[[str], None],
    ) -> dict:
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict] = {}
        with outbound_client(timeout=self.timeout_seconds) as client:
            with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if isinstance(reasoning, str) and reasoning:
                            reasoning_parts.append(reasoning)
                        for tc in delta.get("tool_calls") or []:
                            slot = calls.setdefault(
                                int(tc.get("index") or 0),
                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                            )
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                slot["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                slot["function"]["arguments"] += fn["arguments"]
                        text = delta.get("content")
                        if isinstance(text, str) and text:
                            content_parts.append(text)
                            on_content_delta("".join(content_parts))
        message: dict = {"role": "assistant", "content": "".join(content_parts) or None}
        if reasoning_parts:
            message["reasoning_content"] = "".join(reasoning_parts)
        if calls:
            message["tool_calls"] = [calls[i] for i in sorted(calls)]
        return message


def _parse_assistant_message(message: dict) -> dict:
    tool_calls = []
    for call in message.get("tool_calls") or []:
        fn = call.get("function") or {}
        tool_calls.append(
            {
                "id": call.get("id") or "",
                "name": fn.get("name"),
                "arguments": fn.get("arguments") or "{}",
            }
        )
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    if isinstance(reasoning, str):
        reasoning = reasoning.strip()
    else:
        reasoning = ""
    return {
        "content": (message.get("content") or "").strip(),
        "reasoning": reasoning,
        "tool_calls": tool_calls,
        "raw_assistant_message": message,
    }
