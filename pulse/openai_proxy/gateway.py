"""OpenAI-compatible HTTP gateway (M4)."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, Response
from pulse.openai_proxy.authorize import authorize_pkcp
from pulse.openai_proxy.forward import _openai_error, forward_chat_completions
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def handle_chat_completions(
    request: Request,
    session: Session,
    *,
    encryption_key: str,
) -> Response:
    token = _extract_bearer(request)
    auth = authorize_pkcp(session, token)
    if auth["status"] != "ok":
        reason = auth.get("reason") or auth["status"]
        if reason in ("window_5h_exceeded", "window_7d_exceeded"):
            return _openai_error(429, "Pulse proxy key window limit exceeded", "rate_limit_error")
        return _openai_error(401, "Invalid API key provided")

    vendor = auth["coding_plan_vendor"]
    proxy_key_id = auth["proxy_key_id"]
    assert vendor and proxy_key_id

    try:
        payload = await request.json()
    except Exception:
        return _openai_error(400, "Invalid JSON body")

    return await forward_chat_completions(
        session,
        vendor=vendor,
        payload=payload,
        encryption_key=encryption_key,
        proxy_key_id=proxy_key_id,
    )


def register_openai_gateway_routes(app, get_db, config) -> None:
    enc_key = (config.credentials.encryption_key or "").strip()

    def _require_enc() -> str:
        if not enc_key:
            raise HTTPException(status_code=503, detail="Credential encryption key not configured")
        return enc_key

    @app.post("/openai/v1/chat/completions")
    async def openai_chat_completions(request: Request, session: Session = Depends(get_db)):
        return await handle_chat_completions(request, session, encryption_key=_require_enc())

    @app.get("/openai/v1/models")
    def openai_models_stub(session: Session = Depends(get_db)):
        del session
        return {
            "object": "list",
            "data": [
                {"id": "glm-4.7", "object": "model", "owned_by": "glm"},
                {"id": "glm-5.2", "object": "model", "owned_by": "glm"},
                {"id": "MiniMax-M2.5", "object": "model", "owned_by": "minimax"},
                {"id": "kimi-k2.5", "object": "model", "owned_by": "kimi"},
            ],
        }
