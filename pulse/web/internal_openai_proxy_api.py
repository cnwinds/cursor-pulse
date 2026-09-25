"""Internal API: Go data plane resolves pkcp_ → upstream credential."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.openai_proxy.authorize import authorize_pkcp
from pulse.openai_proxy.pool import pick_cp_credential
from pulse.openai_proxy.upstream import openai_base_url
from pulse.openai_proxy.usage import parse_openai_usage, record_cp_gateway_usage


class OpenAIResolveBody(BaseModel):
    pulse_key: str = Field(min_length=1)
    exclude_credential_ids: list[str] = Field(default_factory=list)


class OpenAIUsageBody(BaseModel):
    proxy_key_id: str
    credential_id: str | None = None
    model: str | None = None
    usage: dict = Field(default_factory=dict)
    request_id: str | None = None


def register_internal_openai_proxy_routes(app, get_db, config) -> None:
    def require_internal_service(
        authorization: Annotated[str | None, Header()] = None,
        x_pulse_internal_token: Annotated[str | None, Header(alias="X-Pulse-Internal-Token")] = None,
    ) -> None:
        expected = (config.internal.service_token or "").strip()
        if not expected:
            raise HTTPException(status_code=503, detail="Internal proxy API not configured")
        provided = ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        elif x_pulse_internal_token:
            provided = x_pulse_internal_token.strip()
        if not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.post(
        "/api/internal/v1/openai-proxy/resolve",
        dependencies=[Depends(require_internal_service)],
    )
    def openai_resolve(body: OpenAIResolveBody, session: Session = Depends(get_db)):
        enc = (config.credentials.encryption_key or "").strip()
        if not enc:
            raise HTTPException(status_code=503, detail="Credential encryption key not configured")
        auth = authorize_pkcp(session, body.pulse_key)
        if auth["status"] != "ok":
            return auth
        vendor = auth["coding_plan_vendor"]
        assert vendor
        entry = pick_cp_credential(
            session,
            vendor_slug=vendor,
            encryption_key=enc,
            exclude_credential_ids=set(body.exclude_credential_ids or []),
        )
        if entry is None:
            return {
                "status": "no_pool",
                "proxy_key_id": auth["proxy_key_id"],
                "coding_plan_vendor": vendor,
                "reason": "empty_pool",
            }
        base = openai_base_url(vendor_slug=vendor, api_region=entry.get("api_region"))
        chat_url = f"{base.rstrip('/')}/chat/completions"
        return {
            "status": "ok",
            "proxy_key_id": auth["proxy_key_id"],
            "coding_plan_vendor": vendor,
            "credential_id": entry["credential_id"],
            "api_key": entry["api_key"],
            "upstream_chat_url": chat_url,
        }

    @app.post(
        "/api/internal/v1/openai-proxy/usage",
        dependencies=[Depends(require_internal_service)],
    )
    def openai_usage(body: OpenAIUsageBody, session: Session = Depends(get_db)):
        tokens = parse_openai_usage({"usage": body.usage})
        record_cp_gateway_usage(
            session,
            proxy_key_id=body.proxy_key_id,
            credential_id=body.credential_id,
            model=body.model,
            tokens=tokens,
            request_id=body.request_id,
        )
        session.commit()
        return {"recorded": 1}
