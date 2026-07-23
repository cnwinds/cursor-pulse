from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.capabilities.executor import CapabilityExecutor
from assistant_platform.capabilities.models import (
    CapabilityAssignmentRow,
    CapabilityDefinitionRow,
    CapabilityPackItemRow,
    CapabilityPackRow,
    CapabilityVersionRow,
)
from assistant_platform.capabilities.pulse_client import PulseCapabilityClient
from assistant_platform.capabilities.resolve import ResolvedCapability, resolve_capabilities
from assistant_platform.config import AssistantConfig


class InvokeCapabilityBody(BaseModel):
    team_id: str
    actor_member_id: str
    role: str | None = None
    capability_key: str
    capability_version: str = "1"
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class CreateAssignmentBody(BaseModel):
    team_id: str
    scope_type: str
    scope_id: str = ""
    pack_id: str | None = None
    capability_key: str | None = None
    capability_version: str | None = None


def _resolved_capability_json(cap: ResolvedCapability) -> dict[str, Any]:
    return {
        "key": cap.key,
        "version": cap.version,
        "risk_level": cap.risk_level,
        "display_name": cap.display_name,
        "description": cap.description,
        "prompt_instruction": cap.prompt_instruction,
        "input_schema": cap.input_schema,
        "confirmation_required": cap.confirmation_required,
    }


def register_capability_routes(
    app,
    *,
    config: AssistantConfig,
    session_factory: sessionmaker[Session],
    require_service_token: Callable[..., None],
) -> None:
    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    @app.post(
        "/api/assistant/v1/capabilities/invoke",
        dependencies=[Depends(require_service_token)],
    )
    def invoke_capability(body: InvokeCapabilityBody, session: Session = Depends(get_db)):
        pulse_client = PulseCapabilityClient(
            base_url=config.pulse_base_url,
            internal_token=config.pulse_internal_token,
        )
        try:
            executor = CapabilityExecutor(
                session=session,
                config=config,
                pulse_client=pulse_client,
            )
            result = executor.invoke(
                actor_member_id=body.actor_member_id,
                team_id=body.team_id,
                role=body.role,
                capability_key=body.capability_key,
                arguments=body.arguments,
                confirmed=body.confirmed,
                capability_version=body.capability_version,
            )
            session.commit()
            return asdict(result)
        finally:
            pulse_client.close()

    @app.get(
        "/api/assistant/v1/capabilities/me",
        dependencies=[Depends(require_service_token)],
    )
    def list_my_capabilities(
        team_id: str,
        member_id: str,
        role: str | None = None,
        channel: str = "dingtalk",
        session: Session = Depends(get_db),
    ):
        resolved = resolve_capabilities(
            session,
            team_id=team_id,
            role=role,
            member_id=member_id,
            channel=channel,
        )
        return [_resolved_capability_json(cap) for cap in resolved]

    @app.get(
        "/api/assistant/v1/capabilities/catalog",
        dependencies=[Depends(require_service_token)],
    )
    def list_capability_catalog(session: Session = Depends(get_db)):
        definitions = session.scalars(select(CapabilityDefinitionRow)).all()
        versions = session.scalars(select(CapabilityVersionRow)).all()
        versions_by_definition: dict[str, list[CapabilityVersionRow]] = {}
        for version_row in versions:
            versions_by_definition.setdefault(version_row.definition_id, []).append(version_row)

        catalog: list[dict[str, Any]] = []
        for definition in definitions:
            for version_row in versions_by_definition.get(definition.id, []):
                catalog.append(
                    {
                        "key": definition.key,
                        "display_name": definition.display_name,
                        "description": definition.description,
                        "definition_status": definition.status,
                        "version": version_row.version,
                        "risk_level": version_row.risk_level,
                        "input_schema": version_row.input_schema_json,
                        "output_schema": version_row.output_schema_json,
                        "provider_type": version_row.provider_type,
                        "provider_operation": version_row.provider_operation,
                        "prompt_instruction": version_row.prompt_instruction,
                        "idempotency_required": version_row.idempotency_required,
                        "timeout_seconds": version_row.timeout_seconds,
                        "version_status": version_row.status,
                    }
                )
        catalog.sort(key=lambda item: (item["key"], item["version"]))
        return catalog

    @app.get(
        "/api/assistant/v1/capabilities/packs",
        dependencies=[Depends(require_service_token)],
    )
    def list_capability_packs(
        team_id: str | None = None,
        session: Session = Depends(get_db),
    ):
        query = select(CapabilityPackRow)
        if team_id:
            query = query.where(CapabilityPackRow.team_id == team_id)
        packs = session.scalars(query.order_by(CapabilityPackRow.key.asc())).all()
        result: list[dict[str, Any]] = []
        for pack in packs:
            items = session.scalars(
                select(CapabilityPackItemRow).where(
                    CapabilityPackItemRow.pack_id == pack.id
                )
            ).all()
            result.append(
                {
                    "id": pack.id,
                    "team_id": pack.team_id,
                    "key": pack.key,
                    "display_name": pack.display_name,
                    "capability_keys": [item.capability_key for item in items],
                    "created_at": pack.created_at.isoformat(),
                }
            )
        return result

    @app.get(
        "/api/assistant/v1/capabilities/assignments",
        dependencies=[Depends(require_service_token)],
    )
    def list_assignments(
        team_id: str | None = None,
        session: Session = Depends(get_db),
    ):
        query = select(CapabilityAssignmentRow)
        if team_id:
            query = query.where(CapabilityAssignmentRow.team_id == team_id)
        rows = session.scalars(
            query.order_by(CapabilityAssignmentRow.created_at.desc())
        ).all()
        return [
            {
                "id": row.id,
                "team_id": row.team_id,
                "scope_type": row.scope_type,
                "scope_id": row.scope_id,
                "pack_id": row.pack_id,
                "capability_key": row.capability_key,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    @app.post(
        "/api/assistant/v1/capabilities/assignments",
        dependencies=[Depends(require_service_token)],
    )
    def create_assignment(body: CreateAssignmentBody, session: Session = Depends(get_db)):
        if not body.pack_id and not body.capability_key:
            raise HTTPException(
                status_code=400,
                detail="pack_id or capability_key is required",
            )
        if body.pack_id and body.capability_key:
            raise HTTPException(
                status_code=400,
                detail="provide either pack_id or capability_key, not both",
            )

        row = CapabilityAssignmentRow(
            team_id=body.team_id,
            scope_type=body.scope_type,
            scope_id=body.scope_id,
            pack_id=body.pack_id,
            capability_key=body.capability_key,
        )
        session.add(row)
        session.commit()
        return {
            "id": row.id,
            "team_id": row.team_id,
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
            "pack_id": row.pack_id,
            "capability_key": row.capability_key,
        }

    @app.delete(
        "/api/assistant/v1/capabilities/assignments/{assignment_id}",
        dependencies=[Depends(require_service_token)],
    )
    def delete_assignment(assignment_id: str, session: Session = Depends(get_db)):
        row = session.get(CapabilityAssignmentRow, assignment_id)
        if row is None:
            raise HTTPException(status_code=404, detail="assignment not found")
        session.delete(row)
        session.commit()
        return {"deleted": True, "id": assignment_id}
