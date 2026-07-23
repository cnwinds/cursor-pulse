from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str) -> str: ...

    def complete_with_image(
        self,
        *,
        system: str,
        user: str,
        image_path,
        model: str | None = None,
    ) -> str: ...


class OpenAICompatibleClient:
    """OpenAI Chat Completions 兼容客户端（OpenAI / Azure / 本地代理均可）。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def complete(self, *, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response: {data!r}") from exc

    def complete_with_tools(
        self,
        *,
        system: str,
        user: str,
        tools: list[dict],
    ) -> dict:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0.1,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        message = data["choices"][0]["message"]
        tool_calls = []
        for call in message.get("tool_calls") or []:
            fn = call.get("function") or {}
            tool_calls.append(
                {
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "{}",
                }
            )
        return {
            "content": (message.get("content") or "").strip(),
            "tool_calls": tool_calls,
        }

    def complete_with_image(
        self,
        *,
        system: str,
        user: str,
        image_path: Path,
        model: str | None = None,
    ) -> str:
        import base64
        import mimetypes

        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        },
                    ],
                },
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response: {data!r}") from exc


def build_llm_client(config) -> LLMClient | None:
    llm = config.llm
    if not llm.api_key:
        return None
    if not (llm.enabled or llm.vision_enabled):
        return None
    return OpenAICompatibleClient(
        api_key=llm.api_key,
        model=llm.model,
        base_url=llm.base_url,
        timeout_seconds=llm.timeout_seconds,
    )
