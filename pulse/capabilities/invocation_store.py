from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.storage.models import CapabilityInvocationRow


def _request_to_dict(request: CapabilityInvokeRequest) -> dict[str, Any]:
    return {
        "invocation_id": request.invocation_id,
        "idempotency_key": request.idempotency_key,
        "team_id": request.team_id,
        "actor_member_id": request.actor_member_id,
        "capability_key": request.capability_key,
        "capability_version": request.capability_version,
        "arguments": request.arguments,
        "confirmed_by": request.confirmed_by,
        "approved_by": request.approved_by,
        "requested_at": request.requested_at,
    }


def _result_to_dict(result: CapabilityInvokeResult) -> dict[str, Any]:
    return asdict(result)


def result_from_dict(data: dict[str, Any]) -> CapabilityInvokeResult:
    return CapabilityInvokeResult(
        status=data["status"],
        user_message=data.get("user_message", ""),
        result=data.get("result") or {},
        error_code=data.get("error_code"),
        retryable=bool(data.get("retryable", False)),
        provider_reference=data.get("provider_reference"),
        completed_at=data.get("completed_at"),
    )


def get_by_idempotency(
    session: Session,
    *,
    team_id: str,
    idempotency_key: str,
) -> CapabilityInvocationRow | None:
    return session.scalar(
        select(CapabilityInvocationRow).where(
            CapabilityInvocationRow.team_id == team_id,
            CapabilityInvocationRow.idempotency_key == idempotency_key,
        )
    )


def get_by_invocation_id(session: Session, invocation_id: str) -> CapabilityInvocationRow | None:
    return session.get(CapabilityInvocationRow, invocation_id)


def save_invocation(
    session: Session,
    *,
    request: CapabilityInvokeRequest,
    result: CapabilityInvokeResult,
) -> CapabilityInvocationRow | None:
    row = CapabilityInvocationRow(
        id=request.invocation_id,
        team_id=request.team_id,
        idempotency_key=request.idempotency_key,
        capability_key=request.capability_key,
        capability_version=request.capability_version,
        actor_member_id=request.actor_member_id,
        request_json=_request_to_dict(request),
        result_json=_result_to_dict(result),
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return None
    return row
