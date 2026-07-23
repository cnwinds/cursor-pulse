from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from assistant_platform.capabilities.catalog import (
    CAPABILITY_OPERATIONS,
    OWNER_EXTRA_KEYS,
    SELF_SERVICE_KEYS,
)
from assistant_platform.capabilities.models import (
    CapabilityAssignmentRow,
    CapabilityDefinitionRow,
    CapabilityPackItemRow,
    CapabilityPackRow,
    CapabilityVersionRow,
)


def _definitions_from_catalog() -> list[dict]:
    defs: list[dict] = []
    for op in CAPABILITY_OPERATIONS:
        defs.append(
            {
                "key": op["capability_key"],
                "display_name": op["display_name"],
                "description": op["description"],
                "version": op["capability_version"],
                "risk_level": op["risk_level"],
                "input_schema_json": op["input_schema"],
                "output_schema_json": op["output_schema"],
                "idempotency_required": op["idempotency_required"],
                "timeout_seconds": op["timeout_seconds"],
                "prompt_instruction": op["description"],
            }
        )
    return defs


_PHASE1_PACKS: list[dict] = [
    {
        "key": "cursor_self_service",
        "display_name": "Cursor 自助服务",
        "capability_keys": list(SELF_SERVICE_KEYS),
    },
    {
        "key": "assistant_owner",
        "display_name": "助手 Owner",
        "capability_keys": list(dict.fromkeys(SELF_SERVICE_KEYS + OWNER_EXTRA_KEYS)),
    },
]


def _get_or_create_definition(session: Session, spec: dict) -> CapabilityDefinitionRow:
    row = session.scalar(
        select(CapabilityDefinitionRow).where(CapabilityDefinitionRow.key == spec["key"])
    )
    if row is not None:
        if row.display_name != spec["display_name"] or row.description != spec["description"]:
            row.display_name = spec["display_name"]
            row.description = spec["description"]
            session.add(row)
            session.flush()
        return row
    row = CapabilityDefinitionRow(
        key=spec["key"],
        display_name=spec["display_name"],
        description=spec["description"],
        status="active",
    )
    session.add(row)
    session.flush()
    return row


def _get_or_create_version(
    session: Session, definition: CapabilityDefinitionRow, spec: dict
) -> CapabilityVersionRow:
    row = session.scalar(
        select(CapabilityVersionRow).where(
            CapabilityVersionRow.definition_id == definition.id,
            CapabilityVersionRow.version == spec["version"],
        )
    )
    if row is not None:
        if row.prompt_instruction != spec["prompt_instruction"]:
            row.prompt_instruction = spec["prompt_instruction"]
            session.add(row)
            session.flush()
        return row
    row = CapabilityVersionRow(
        definition_id=definition.id,
        version=spec["version"],
        risk_level=spec["risk_level"],
        input_schema_json=spec["input_schema_json"],
        output_schema_json=spec["output_schema_json"],
        provider_type="pulse_http",
        provider_operation=spec["key"],
        prompt_instruction=spec["prompt_instruction"],
        idempotency_required=spec["idempotency_required"],
        timeout_seconds=spec["timeout_seconds"],
        status="active",
    )
    session.add(row)
    session.flush()
    return row


def _get_or_create_pack(session: Session, team_id: str, spec: dict) -> CapabilityPackRow:
    row = session.scalar(
        select(CapabilityPackRow).where(
            CapabilityPackRow.team_id == team_id,
            CapabilityPackRow.key == spec["key"],
        )
    )
    if row is not None:
        return row
    row = CapabilityPackRow(
        team_id=team_id,
        key=spec["key"],
        display_name=spec["display_name"],
    )
    session.add(row)
    session.flush()
    return row


def _get_or_create_pack_item(
    session: Session, pack_id: str, capability_key: str, capability_version: str = "1"
) -> CapabilityPackItemRow:
    row = session.scalar(
        select(CapabilityPackItemRow).where(
            CapabilityPackItemRow.pack_id == pack_id,
            CapabilityPackItemRow.capability_key == capability_key,
        )
    )
    if row is not None:
        return row
    row = CapabilityPackItemRow(
        pack_id=pack_id,
        capability_key=capability_key,
        capability_version=capability_version,
    )
    session.add(row)
    session.flush()
    return row


def _pack_dedupe_score(session: Session, pack: CapabilityPackRow) -> tuple[int, int, float]:
    item_count = (
        session.scalar(
            select(func.count())
            .select_from(CapabilityPackItemRow)
            .where(CapabilityPackItemRow.pack_id == pack.id)
        )
        or 0
    )
    assignment_refs = (
        session.scalar(
            select(func.count())
            .select_from(CapabilityAssignmentRow)
            .where(CapabilityAssignmentRow.pack_id == pack.id)
        )
        or 0
    )
    ts = pack.created_at.timestamp() if pack.created_at else 0.0
    return (int(item_count), int(assignment_refs), -ts)


def _dedupe_packs_for_team(session: Session, team_id: str, pack_keys: list[str]) -> None:
    for key in pack_keys:
        packs = list(
            session.scalars(
                select(CapabilityPackRow).where(
                    CapabilityPackRow.team_id == team_id,
                    CapabilityPackRow.key == key,
                )
            ).all()
        )
        if len(packs) <= 1:
            continue

        canonical = max(packs, key=lambda p: _pack_dedupe_score(session, p))
        for pack in packs:
            if pack.id == canonical.id:
                continue
            for assignment in session.scalars(
                select(CapabilityAssignmentRow).where(
                    CapabilityAssignmentRow.pack_id == pack.id
                )
            ).all():
                existing = session.scalar(
                    select(CapabilityAssignmentRow).where(
                        CapabilityAssignmentRow.team_id == team_id,
                        CapabilityAssignmentRow.scope_type == assignment.scope_type,
                        CapabilityAssignmentRow.scope_id == assignment.scope_id,
                        CapabilityAssignmentRow.pack_id == canonical.id,
                    )
                )
                if existing is not None:
                    session.delete(assignment)
                else:
                    assignment.pack_id = canonical.id
            for item in session.scalars(
                select(CapabilityPackItemRow).where(CapabilityPackItemRow.pack_id == pack.id)
            ).all():
                session.delete(item)
            session.delete(pack)
        session.flush()

    scopes = session.execute(
        select(
            CapabilityAssignmentRow.scope_type,
            CapabilityAssignmentRow.scope_id,
        ).where(
            CapabilityAssignmentRow.team_id == team_id,
            CapabilityAssignmentRow.pack_id.is_not(None),
        )
    ).all()
    seen: set[tuple[str, str]] = set()
    for scope_type, scope_id in scopes:
        scope_key = (scope_type, scope_id or "")
        if scope_key in seen:
            continue
        seen.add(scope_key)
        rows = list(
            session.scalars(
                select(CapabilityAssignmentRow).where(
                    CapabilityAssignmentRow.team_id == team_id,
                    CapabilityAssignmentRow.scope_type == scope_type,
                    CapabilityAssignmentRow.scope_id == (scope_id or ""),
                    CapabilityAssignmentRow.pack_id.is_not(None),
                )
            ).all()
        )
        if len(rows) <= 1:
            continue
        for extra in rows[1:]:
            session.delete(extra)
    session.flush()


def _get_or_create_pack_assignment(
    session: Session,
    *,
    team_id: str,
    scope_type: str,
    scope_id: str,
    pack_id: str,
) -> CapabilityAssignmentRow:
    row = session.scalar(
        select(CapabilityAssignmentRow).where(
            CapabilityAssignmentRow.team_id == team_id,
            CapabilityAssignmentRow.scope_type == scope_type,
            CapabilityAssignmentRow.scope_id == scope_id,
            CapabilityAssignmentRow.pack_id == pack_id,
        )
    )
    if row is not None:
        return row
    row = CapabilityAssignmentRow(
        team_id=team_id,
        scope_type=scope_type,
        scope_id=scope_id,
        pack_id=pack_id,
    )
    session.add(row)
    session.flush()
    return row


def seed_phase1_capabilities(session: Session, team_id: str) -> None:
    """Idempotently seed capability definitions, packs, and assignments."""
    for spec in _definitions_from_catalog():
        definition = _get_or_create_definition(session, spec)
        _get_or_create_version(session, definition, spec)

    _dedupe_packs_for_team(session, team_id, [p["key"] for p in _PHASE1_PACKS])

    packs_by_key: dict[str, CapabilityPackRow] = {}
    for pack_spec in _PHASE1_PACKS:
        pack = _get_or_create_pack(session, team_id, pack_spec)
        packs_by_key[pack.key] = pack
        for capability_key in pack_spec["capability_keys"]:
            _get_or_create_pack_item(session, pack.id, capability_key)

    _get_or_create_pack_assignment(
        session,
        team_id=team_id,
        scope_type="team_default",
        scope_id="",
        pack_id=packs_by_key["cursor_self_service"].id,
    )
    _get_or_create_pack_assignment(
        session,
        team_id=team_id,
        scope_type="role_pack",
        scope_id="owner",
        pack_id=packs_by_key["assistant_owner"].id,
    )
    _get_or_create_pack_assignment(
        session,
        team_id=team_id,
        scope_type="role_pack",
        scope_id="operator",
        pack_id=packs_by_key["assistant_owner"].id,
    )
