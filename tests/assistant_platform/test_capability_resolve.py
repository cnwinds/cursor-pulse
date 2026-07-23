from __future__ import annotations

from sqlalchemy import select

from assistant_platform.capabilities.catalog import OWNER_EXTRA_KEYS, SELF_SERVICE_KEYS
from assistant_platform.capabilities.models import (
    CapabilityAssignmentRow,
    CapabilityDefinitionRow,
)
from assistant_platform.capabilities.resolve import resolve_capabilities
from assistant_platform.capabilities.seed import seed_phase1_capabilities
from assistant_platform.storage.db import init_assistant_db

TEAM_ID = "team-resolve"

OWNER_KEYS = set(dict.fromkeys(SELF_SERVICE_KEYS + OWNER_EXTRA_KEYS))


def _session():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    return Session()


def _keys(resolved) -> set[str]:
    return {cap.key for cap in resolved}


def _insert_assignment(
    session,
    *,
    team_id: str,
    scope_type: str,
    scope_id: str,
    pack_id: str | None = None,
    capability_key: str | None = None,
) -> CapabilityAssignmentRow:
    row = CapabilityAssignmentRow(
        team_id=team_id,
        scope_type=scope_type,
        scope_id=scope_id,
        pack_id=pack_id,
        capability_key=capability_key,
    )
    session.add(row)
    session.flush()
    return row


def _insert_user_deny(
    session,
    *,
    team_id: str,
    member_id: str,
    capability_key: str | None = None,
    pack_id: str | None = None,
) -> CapabilityAssignmentRow:
    return _insert_assignment(
        session,
        team_id=team_id,
        scope_type="user_deny",
        scope_id=member_id,
        capability_key=capability_key,
        pack_id=pack_id,
    )


def _insert_user_allow(
    session,
    *,
    team_id: str,
    member_id: str,
    capability_key: str | None = None,
    pack_id: str | None = None,
) -> CapabilityAssignmentRow:
    return _insert_assignment(
        session,
        team_id=team_id,
        scope_type="user_allow",
        scope_id=member_id,
        capability_key=capability_key,
        pack_id=pack_id,
    )


def test_default_member_gets_quota_and_bind_not_guide():
    session = _session()
    for role in (None, "ai_member"):
        resolved = resolve_capabilities(
            session,
            team_id=TEAM_ID,
            role=role,
            member_id="member-default",
        )
        assert _keys(resolved) == set(SELF_SERVICE_KEYS)


def test_owner_role_includes_guide():
    session = _session()
    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="owner",
        member_id="member-owner",
    )
    assert _keys(resolved) == OWNER_KEYS


def test_user_deny_bind_removes_bind():
    session = _session()
    _insert_user_deny(
        session,
        team_id=TEAM_ID,
        member_id="member-denied",
        capability_key="cursor.key.bind",
    )
    session.commit()

    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="ai_member",
        member_id="member-denied",
    )
    assert _keys(resolved) == set(SELF_SERVICE_KEYS) - {"cursor.key.bind"}


def test_user_allow_guide_for_non_owner():
    session = _session()
    _insert_user_allow(
        session,
        team_id=TEAM_ID,
        member_id="member-allowed",
        capability_key="guide_image.update",
    )
    session.commit()

    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="ai_member",
        member_id="member-allowed",
    )
    assert _keys(resolved) == set(SELF_SERVICE_KEYS) | {"guide_image.update"}


def test_disabled_definition_disappears():
    session = _session()
    definition = session.scalar(
        select(CapabilityDefinitionRow).where(
            CapabilityDefinitionRow.key == "cursor.key.bind"
        )
    )
    assert definition is not None
    definition.status = "disabled"
    session.commit()

    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="ai_member",
        member_id="member-disabled-def",
    )
    assert _keys(resolved) == set(SELF_SERVICE_KEYS) - {"cursor.key.bind"}


def test_user_allow_cannot_revive_disabled_definition():
    session = _session()
    definition = session.scalar(
        select(CapabilityDefinitionRow).where(
            CapabilityDefinitionRow.key == "guide_image.update"
        )
    )
    assert definition is not None
    definition.status = "disabled"
    _insert_user_allow(
        session,
        team_id=TEAM_ID,
        member_id="member-revive",
        capability_key="guide_image.update",
    )
    session.commit()

    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="ai_member",
        member_id="member-revive",
    )
    assert "guide_image.update" not in _keys(resolved)


def test_confirmation_required_for_sensitive_and_destructive():
    session = _session()
    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="owner",
        member_id="member-confirm",
    )
    by_key = {cap.key: cap for cap in resolved}
    assert by_key["quota.self.read"].confirmation_required is False
    assert by_key["cursor.key.bind"].confirmation_required is True
    assert by_key["guide_image.update"].confirmation_required is True


def test_resolved_capabilities_sorted_by_key():
    session = _session()
    resolved = resolve_capabilities(
        session,
        team_id=TEAM_ID,
        role="owner",
        member_id="member-sort",
    )
    assert [cap.key for cap in resolved] == sorted(cap.key for cap in resolved)
