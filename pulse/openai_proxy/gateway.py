"""OpenAI-compatible HTTP gateway (M4)."""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import Depends, HTTPException, Request, Response
from pulse.openai_proxy.authorize import authorize_pkcp
from pulse.openai_proxy.pool import pick_cp_credential
from pulse.openai_proxy.upstream import auth_header, openai_base_url
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _openai_error(status: int, message: str, err_type: str = "invalid_request_error") -> Response:
    body = {"error": {"message": message, "type": err_type}}
    return Response(content=json.dumps(body), status_code=status, media_type="application/json")


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
    assert vendor
    entry = pick_cp_credential(session, vendor_slug=vendor, encryption_key=encryption_key)
    if entry is None:
        return _openai_error(
            503,
            f"No available Coding Plan account in pool for {vendor}",
            "server_error",
        )

    try:
        payload = await request.json()
    except Exception:
        return _openai_error(400, "Invalid JSON body")

    base = openai_base_url(vendor_slug=vendor, api_region=entry.get("api_region"))
    url = f"{base.rstrip('/')}/chat/completions"
    headers = auth_header(vendor_slug=vendor, api_key=entry["api_key"])
    stream = bool(payload.get("stream"))

    timeout = httpx.Timeout(600.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            if stream:
                req = client.build_request("POST", url, headers=headers, json=payload)
                resp = await client.send(req, stream=True)
                if resp.status_code >= 400:
                    err_body = await resp.aread()
                    await resp.aclose()
                    return Response(
                        content=err_body,
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type"),
                    )

                async def _iter():
                    try:
                        async for chunk in resp.aiter_bytes():
                            yield chunk
                    finally:
                        await resp.aclose()

                return StreamingResponse(
                    _iter(),
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "text/event-stream"),
                )
            resp = await client.post(url, headers=headers, json=payload)
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("content-type", "application/json"),
            )
        except httpx.HTTPError as exc:
            logger.warning("openai gateway upstream error vendor=%s: %s", vendor, exc)
            return _openai_error(502, "Upstream request failed", "server_error")


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
                {"id": "glm-coding-plan", "object": "model", "owned_by": "pulse"},
                {"id": "minimax-coding-plan", "object": "model", "owned_by": "pulse"},
            ],
        }
