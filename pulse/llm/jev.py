"""TypeSafe Jev (System One) HTTP client. No SDK dependency."""

from __future__ import annotations

import logging
from typing import Any

from pulse.http_clients import outbound_client

logger = logging.getLogger(__name__)

SYSTEMONE_PATH = "/v1/systemone"


class JevError(RuntimeError):
    """TypeSafe evaluate call failed (caller should fail open)."""


class JevClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.typesafe.ai",
        model: str = "jev-latest",
        timeout_seconds: float = 8.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    def system_one(
        self,
        *,
        state: Any,
        questions: dict[str, dict],
        model: str | None = None,
    ) -> dict:
        url = f"{self.base_url}{SYSTEMONE_PATH}"
        payload = {
            "state": state,
            "model": model or self.model,
            "questions": questions,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with outbound_client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            try:
                response.raise_for_status()
            except Exception as exc:
                body = (response.text or "")[:300]
                raise JevError(f"Jev HTTP {response.status_code}: {body}") from exc
            data = response.json()
        if not isinstance(data, dict) or "answers" not in data:
            raise JevError(f"Unexpected Jev response: {data!r}")
        return data


def build_jev_client(config) -> JevClient | None:
    jev = getattr(getattr(config, "tool_center", None), "jev", None)
    if jev is None:
        return None
    api_key = (jev.api_key or "").strip()
    if not jev.enabled or not api_key:
        return None
    return JevClient(
        api_key=api_key,
        base_url=jev.base_url or "https://api.typesafe.ai",
        model=jev.model or "jev-latest",
        timeout_seconds=float(jev.timeout_seconds or 8.0),
    )
