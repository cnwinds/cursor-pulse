from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.capabilities.models import (
    CapabilityAssignmentRow,
    CapabilityDefinitionRow,
    CapabilityPackItemRow,
    CapabilityVersionRow,
)

_SENSITIVE_RISK_LEVELS = frozenset({"sensitive", "destructive"})


@dataclass
class ResolvedCapability:
    key: str
    version: str
    risk_level: str
    display_name: str = ""
    description: str = ""
    prompt_instruction: str = ""
    input_schema: dict | None = None
    confirmation_required: bool = False


def _is_active_capability(
    definitions: dict[str, CapabilityDefinitionRow],
    versions: dict[tuple[str, str], CapabilityVersionRow],
    key: str,
    version: str,
) -> bool:
    definition = definitions.get(key)
    if definition is None or definition.status != "active":
        return False
    version_row = versions.get((definition.id, version))
    return version_row is not None and version_row.status == "active"


def _pack_items(session: Session, pack_id: str) -> list[tuple[str, str]]:
    rows = session.scalars(
        select(CapabilityPackItemRow).where(CapabilityPackItemRow.pack_id == pack_id)
    ).all()
    return [(row.capability_key, row.capability_version) for row in rows]


def _assignment_capabilities(
    session: Session, assignment: CapabilityAssignmentRow
) -> list[tuple[str, str]]:
    if assignment.pack_id:
        return _pack_items(session, assignment.pack_id)
    if assignment.capability_key:
        return [(assignment.capability_key, "1")]
    return []


def _apply_assignments(
    session: Session,
    assignments: list[CapabilityAssignmentRow],
    granted: dict[str, str],
    *,
    add: bool,
) -> None:
    for assignment in assignments:
        for key, version in _assignment_capabilities(session, assignment):
            if add:
                granted[key] = version
            else:
                granted.pop(key, None)


def resolve_capabilities(
    session: Session,
    *,
    team_id: str,
    role: str | None,
    member_id: str | None = None,
    channel: str = "dingtalk",
) -> list[ResolvedCapability]:
    del channel  # reserved for supported_channels filtering when schema adds it

    definitions = {
        row.key: row
        for row in session.scalars(select(CapabilityDefinitionRow)).all()
    }
    versions: dict[tuple[str, str], CapabilityVersionRow] = {}
    for version_row in session.scalars(select(CapabilityVersionRow)).all():
        versions[(version_row.definition_id, version_row.version)] = version_row

    assignments = session.scalars(
        select(CapabilityAssignmentRow).where(
            CapabilityAssignmentRow.team_id == team_id
        )
    ).all()

    granted: dict[str, str] = {}

    team_default = [a for a in assignments if a.scope_type == "team_default"]
    _apply_assignments(session, team_default, granted, add=True)

    if role:
        role_packs = [
            a
            for a in assignments
            if a.scope_type == "role_pack" and a.scope_id == role
        ]
        _apply_assignments(session, role_packs, granted, add=True)

    if member_id:
        user_denies = [
            a
            for a in assignments
            if a.scope_type == "user_deny" and a.scope_id == member_id
        ]
        _apply_assignments(session, user_denies, granted, add=False)

        user_allows = [
            a
            for a in assignments
            if a.scope_type == "user_allow" and a.scope_id == member_id
        ]
        for assignment in user_allows:
            for key, version in _assignment_capabilities(session, assignment):
                if _is_active_capability(definitions, versions, key, version):
                    granted[key] = version

    resolved: list[ResolvedCapability] = []
    for key in sorted(granted):
        version = granted[key]
        if not _is_active_capability(definitions, versions, key, version):
            continue
        definition = definitions[key]
        version_row = versions[(definition.id, version)]
        resolved.append(
            ResolvedCapability(
                key=key,
                version=version,
                risk_level=version_row.risk_level,
                display_name=definition.display_name,
                description=definition.description,
                prompt_instruction=version_row.prompt_instruction,
                input_schema=version_row.input_schema_json,
                confirmation_required=version_row.risk_level in _SENSITIVE_RISK_LEVELS,
            )
        )

    return resolved
