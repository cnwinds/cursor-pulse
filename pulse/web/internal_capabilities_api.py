from __future__ import annotations

import hmac
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.capabilities.invoke import invoke_capability
from pulse.capabilities.invocation_store import (
    get_by_idempotency,
    get_by_invocation_id,
    result_from_dict,
    save_invocation,
)
from pulse.capabilities.manifest import list_operations
from pulse.capabilities.routing_metrics import snapshot as routing_metrics_snapshot
from pulse.config import AppConfig
from pulse.web.settings_store import effective_config


class CapabilityInvokeRequestBody(BaseModel):
    invocation_id: str
    idempotency_key: str
    team_id: str
    actor_member_id: str
    capability_key: str
    capability_version: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed_by: str | None = None
    approved_by: str | None = None
    requested_at: str | None = None


class CapabilityInvokeResponseBody(BaseModel):
    status: str
    user_message: str = ""
    result: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False
    provider_reference: str | None = None
    completed_at: str | None = None


def _to_response(result: CapabilityInvokeResult) -> CapabilityInvokeResponseBody:
    from pulse.channels.capability_bridge import format_capability_reply

    data = asdict(result)
    if not (data.get("user_message") or "").strip():
        data["user_message"] = format_capability_reply(result)
    # result may be None on some failed paths
    if data.get("result") is None:
        data["result"] = {}
    return CapabilityInvokeResponseBody(**data)


def _to_request(body: CapabilityInvokeRequestBody) -> CapabilityInvokeRequest:
    return CapabilityInvokeRequest(
        invocation_id=body.invocation_id,
        idempotency_key=body.idempotency_key,
        team_id=body.team_id,
        actor_member_id=body.actor_member_id,
        capability_key=body.capability_key,
        capability_version=body.capability_version,
        arguments=body.arguments,
        confirmed_by=body.confirmed_by,
        approved_by=body.approved_by,
        requested_at=body.requested_at,
    )


def register_internal_capabilities_routes(app, get_db, config: AppConfig) -> None:
    def require_internal_service(
        authorization: Annotated[str | None, Header()] = None,
        x_pulse_internal_token: Annotated[str | None, Header(alias="X-Pulse-Internal-Token")] = None,
    ) -> None:
        expected = (config.internal.service_token or "").strip()
        if not expected:
            raise HTTPException(
                status_code=503,
                detail="Internal capability API not configured",
            )
        provided = ""
        if authorization and authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
        elif x_pulse_internal_token:
            provided = x_pulse_internal_token.strip()
        if not provided or not hmac.compare_digest(provided, expected):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get(
        "/api/internal/v1/capabilities/manifest",
        dependencies=[Depends(require_internal_service)],
    )
    def internal_capabilities_manifest():
        return {"operations": list_operations()}

    @app.get(
        "/api/internal/v1/capabilities/routing-metrics",
        dependencies=[Depends(require_internal_service)],
    )
    def internal_capabilities_routing_metrics():
        return routing_metrics_snapshot()

    @app.post(
        "/api/internal/v1/capabilities/invoke",
        dependencies=[Depends(require_internal_service)],
        response_model=CapabilityInvokeResponseBody,
    )
    def internal_capabilities_invoke(
        body: CapabilityInvokeRequestBody,
        session: Session = Depends(get_db),
    ):
        request = _to_request(body)
        existing = get_by_idempotency(
            session,
            team_id=request.team_id,
            idempotency_key=request.idempotency_key,
        )
        if existing is not None:
            return _to_response(result_from_dict(existing.result_json))

        runtime_config = effective_config(config, session, body.team_id)
        result = invoke_capability(session, request=request, config=runtime_config)
        saved = save_invocation(session, request=request, result=result)
        if saved is None:
            existing = get_by_idempotency(
                session,
                team_id=request.team_id,
                idempotency_key=request.idempotency_key,
            )
            if existing is None:
                raise HTTPException(
                    status_code=500,
                    detail="Idempotency conflict but no stored invocation found",
                )
            return _to_response(result_from_dict(existing.result_json))
        session.commit()
        return _to_response(result)

    @app.get(
        "/api/internal/v1/capabilities/invocations/{invocation_id}",
        dependencies=[Depends(require_internal_service)],
        response_model=CapabilityInvokeResponseBody,
    )
    def internal_capabilities_invocation(
        invocation_id: str,
        session: Session = Depends(get_db),
    ):
        row = get_by_invocation_id(session, invocation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Invocation not found")
        return _to_response(result_from_dict(row.result_json))
