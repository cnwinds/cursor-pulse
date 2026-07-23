from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.proxy import service as proxy_service


class AuthorizeBody(BaseModel):
    pulse_key: str


class UsageItem(BaseModel):
    proxy_key_id: str | None = None
    loan_id: str | None = None
    credential_id: str | None = None
    model: str | None = None
    tokens: dict[str, int] = {}
    ts: datetime | None = None
    request_id: str | None = None


class UsageBody(BaseModel):
    items: list[UsageItem] = Field(default_factory=list, max_length=1000)


class EventItem(BaseModel):
    event_type: str
    proxy_key_id: str | None = None
    loan_id: str | None = None
    credential_id: str | None = None
    detail: str | None = None


class EventsBody(BaseModel):
    events: list[EventItem] = Field(default_factory=list, max_length=1000)


def register_internal_proxy_routes(app, get_db, config) -> None:
    def require_internal_service(
        authorization: Annotated[str | None, Header()] = None,
        x_pulse_internal_token: Annotated[
            str | None, Header(alias="X-Pulse-Internal-Token")
        ] = None,
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
        "/api/internal/v1/proxy/authorize",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_authorize(body: AuthorizeBody, session: Session = Depends(get_db)):
        enc_key = (config.credentials.encryption_key or "").strip()
        return proxy_service.authorize_status(
            session, body.pulse_key, encryption_key=enc_key
        )

    @app.get(
        "/api/internal/v1/proxy/pool",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_pool(session: Session = Depends(get_db)):
        enc_key = (config.credentials.encryption_key or "").strip()
        if not enc_key:
            raise HTTPException(status_code=503, detail="Credential encryption key not configured")
        credentials = proxy_service.list_pool_credentials(
            session,
            encryption_key=enc_key,
            loan_selection=config.tool_center.loan_selection,
        )
        return {"credentials": credentials}

    @app.post(
        "/api/internal/v1/proxy/usage",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_usage(body: UsageBody, session: Session = Depends(get_db)):
        result = proxy_service.record_usages(
            session, [item.model_dump() for item in body.items]
        )
        session.commit()
        return result

    @app.post(
        "/api/internal/v1/proxy/events",
        dependencies=[Depends(require_internal_service)],
    )
    def proxy_events(body: EventsBody, session: Session = Depends(get_db)):
        for event in body.events:
            proxy_service.record_event(
                session,
                event_type=event.event_type,
                proxy_key_id=event.proxy_key_id,
                loan_id=event.loan_id,
                credential_id=event.credential_id,
                detail=event.detail,
            )
        session.commit()
        return {"recorded": len(body.events)}
