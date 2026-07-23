from __future__ import annotations

import hmac
from typing import Annotated, Callable

from fastapi import Header, HTTPException

from assistant_platform.config import AssistantConfig


def build_require_service_token(config: AssistantConfig) -> Callable[..., None]:
    """FastAPI dependency: reject unconfigured or invalid service tokens."""

    def require_service_token(
        authorization: Annotated[str | None, Header()] = None,
        x_assistant_token: Annotated[str | None, Header(alias="X-Assistant-Token")] = None,
    ) -> None:
        expected = (config.service_token or "").strip()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="Assistant API not configured",
            )

        token: str | None = None
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        elif x_assistant_token:
            token = x_assistant_token.strip()

        if not token or not hmac.compare_digest(token, expected):
            raise HTTPException(status_code=401, detail="invalid service token")

    return require_service_token
