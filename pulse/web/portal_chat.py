from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import PortalChatDelivery


def store_portal_chat_delivery(
    session: Session,
    *,
    team_id: str,
    member_id: str,
    text: str,
    kind: str = "final",
    assistant_session_id: str | None = None,
    assistant_message_id: str | None = None,
) -> PortalChatDelivery:
    row = PortalChatDelivery(
        team_id=team_id,
        member_id=member_id,
        text=text,
        kind=kind or "final",
        assistant_session_id=assistant_session_id,
        assistant_message_id=assistant_message_id,
    )
    session.add(row)
    session.flush()
    return row


def list_portal_chat_deliveries(
    session: Session,
    *,
    team_id: str,
    member_id: str,
    after_id: int = 0,
    limit: int = 50,
) -> list[PortalChatDelivery]:
    stmt = (
        select(PortalChatDelivery)
        .where(
            PortalChatDelivery.team_id == team_id,
            PortalChatDelivery.member_id == member_id,
            PortalChatDelivery.id > after_id,
        )
        .order_by(PortalChatDelivery.id.asc())
        .limit(limit)
    )
    return list(session.scalars(stmt))


def delivery_to_json(row: PortalChatDelivery) -> dict:
    return {
        "id": row.id,
        "text": row.text,
        "kind": row.kind,
        "session_id": row.assistant_session_id,
        "message_id": row.assistant_message_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
