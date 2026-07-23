from __future__ import annotations

import httpx


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
        with httpx.Client(timeout=self.timeout_seconds) as client:
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
    ) -> dict:
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
                    "id": call.get("id") or "",
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "{}",
                }
            )
        reasoning = (
            message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        )
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
