"""Forward chat completions with pool failover."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import Response
from pulse.openai_proxy.pool import pick_cp_credential
from pulse.openai_proxy.upstream import auth_header, openai_base_url
from pulse.openai_proxy.usage import parse_openai_usage, record_cp_gateway_usage
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 502, 503, 529})
MAX_POOL_ATTEMPTS = 8


def _openai_error(status: int, message: str, err_type: str = "invalid_request_error") -> Response:
    body = {"error": {"message": message, "type": err_type}}
    return Response(content=json.dumps(body), status_code=status, media_type="application/json")


async def forward_chat_completions(
    session: Session,
    *,
    vendor: str,
    payload: dict[str, Any],
    encryption_key: str,
    proxy_key_id: str,
) -> Response:
    stream = bool(payload.get("stream"))
    model = payload.get("model")
    if isinstance(model, str):
        model = model.strip() or None
    else:
        model = None

    excluded: set[str] = set()
    last_status = 503
    timeout = httpx.Timeout(600.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for _attempt in range(MAX_POOL_ATTEMPTS):
            entry = pick_cp_credential(
                session,
                vendor_slug=vendor,
                encryption_key=encryption_key,
                exclude_credential_ids=excluded,
            )
            if entry is None:
                break
            base = openai_base_url(vendor_slug=vendor, api_region=entry.get("api_region"))
            url = f"{base.rstrip('/')}/chat/completions"
            headers = auth_header(vendor_slug=vendor, api_key=entry["api_key"])

            try:
                if stream:
                    req = client.build_request("POST", url, headers=headers, json=payload)
                    resp = await client.send(req, stream=True)
                    if resp.status_code in RETRY_STATUSES:
                        await resp.aread()
                        await resp.aclose()
                        excluded.add(entry["credential_id"])
                        last_status = resp.status_code
                        continue
                    if resp.status_code >= 400:
                        err_body = await resp.aread()
                        await resp.aclose()
                        return Response(
                            content=err_body,
                            status_code=resp.status_code,
                            media_type=resp.headers.get("content-type"),
                        )

                    cred_id = entry["credential_id"]

                    async def _iter(r=resp, pk=proxy_key_id, cid=cred_id, m=model):
                        try:
                            async for chunk in r.aiter_bytes():
                                yield chunk
                        finally:
                            await r.aclose()

                    return StreamingResponse(
                        _iter(),
                        status_code=resp.status_code,
                        media_type=resp.headers.get("content-type", "text/event-stream"),
                    )

                resp = await client.post(url, headers=headers, json=payload)
                last_status = resp.status_code
                if resp.status_code in RETRY_STATUSES:
                    excluded.add(entry["credential_id"])
                    continue
                if resp.status_code == 200:
                    try:
                        body = resp.json()
                        tokens = parse_openai_usage(body)
                        record_cp_gateway_usage(
                            session,
                            proxy_key_id=proxy_key_id,
                            credential_id=entry["credential_id"],
                            model=model or (body.get("model") if isinstance(body.get("model"), str) else None),
                            tokens=tokens,
                        )
                        session.commit()
                    except Exception:
                        logger.exception("cp gateway usage record failed")
                        session.rollback()
                return Response(
                    content=resp.content,
                    status_code=resp.status_code,
                    media_type=resp.headers.get("content-type", "application/json"),
                )
            except httpx.HTTPError as exc:
                logger.warning("cp gateway upstream error: %s", exc)
                excluded.add(entry["credential_id"])
                last_status = 502
                continue

    if excluded:
        return _openai_error(
            last_status if last_status in RETRY_STATUSES else 503,
            "All Coding Plan pool accounts rejected or unavailable",
            "rate_limit_error" if last_status == 429 else "server_error",
        )
    return _openai_error(503, f"No available Coding Plan account in pool for {vendor}", "server_error")
