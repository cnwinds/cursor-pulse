from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import AdminAuditLog


def log_admin_action(
    session: Session,
    *,
    team_id: str,
    member_id: str | None,
    action: str,
    capability: str | None = None,
    detail: str | None = None,
    channel: str = "web",
) -> AdminAuditLog:
    row = AdminAuditLog(
        team_id=team_id,
        member_id=member_id,
        channel=channel,
        action=action,
        capability=capability,
        detail=detail,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return row


def list_admin_audit_logs(
    session: Session,
    team_id: str,
    *,
    limit: int = 100,
) -> list[dict]:
    rows = session.scalars(
        select(AdminAuditLog)
        .where(AdminAuditLog.team_id == team_id)
        .order_by(AdminAuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "member_id": row.member_id,
            "channel": row.channel,
            "action": row.action,
            "capability": row.capability,
            "detail": row.detail,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
