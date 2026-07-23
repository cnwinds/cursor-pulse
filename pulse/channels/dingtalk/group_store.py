from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from pulse.storage.models import Team
from pulse.settings import patch_team_setting


def _binding_from_team_settings(data: dict[str, Any]) -> dict[str, str | None]:
    return {
        "open_conversation_id": data.get("group_open_conversation_id") or None,
        "chat_id": data.get("chat_id") or None,
        "title": data.get("group_title") or None,
    }


def _binding_patch(
    *,
    open_conversation_id: str,
    chat_id: str | None = None,
    title: str | None = None,
) -> dict[str, str]:
    patch: dict[str, str] = {"group_open_conversation_id": open_conversation_id}
    if chat_id:
        patch["chat_id"] = chat_id
    if title:
        patch["group_title"] = title
    return patch


def load_persisted_group_binding(
    *,
    team_slug: str = "default",
    database_url: str | None = None,
) -> dict[str, str | None]:
    from pulse.team_settings_loader import pulse_database_url, read_team_setting_section

    url = database_url or pulse_database_url()
    overrides = read_team_setting_section(
        team_slug=team_slug,
        section="dingtalk",
        database_url=url,
    )
    return _binding_from_team_settings(overrides)


def load_persisted_group_id(
    *,
    team_slug: str = "default",
    database_url: str | None = None,
) -> str | None:
    return load_persisted_group_binding(
        team_slug=team_slug,
        database_url=database_url,
    ).get("open_conversation_id") or None


def save_group_binding(
    *,
    open_conversation_id: str,
    chat_id: str | None = None,
    title: str | None = None,
    team_slug: str = "default",
    database_url: str | None = None,
    member_id: str | None = None,
    session: Session | None = None,
    team_id: str | None = None,
) -> None:
    patch = _binding_patch(
        open_conversation_id=open_conversation_id,
        chat_id=chat_id,
        title=title,
    )
    if session is not None and team_id:
        patch_team_setting(
            session,
            team_id=team_id,
            section="dingtalk",
            patch=patch,
            member_id=member_id,
        )
        return

    from pulse.team_settings_loader import pulse_database_url

    url = database_url or pulse_database_url()
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as owned_session:
        resolved_team_id = team_id or _resolve_team_id(owned_session, team_slug)
        if not resolved_team_id:
            from pulse.tenant.service import get_or_create_team

            team = get_or_create_team(owned_session, team_slug)
            owned_session.flush()
            resolved_team_id = team.id
        patch_team_setting(
            owned_session,
            team_id=resolved_team_id,
            section="dingtalk",
            patch=patch,
            member_id=member_id,
        )
        owned_session.commit()


def _resolve_team_id(session: Session, team_slug: str) -> str | None:
    team = session.scalar(select(Team).where(Team.slug == team_slug).limit(1))
    return team.id if team else None
