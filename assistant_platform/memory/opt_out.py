"""Per-user memory opt-out: stop new archive/distillation while retaining existing data."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from assistant_platform.storage.models import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryOptOutRow(Base):
    __tablename__ = "ap_memory_opt_outs"
    __table_args__ = (UniqueConstraint("user_id", "team_id", name="uq_ap_memory_opt_outs_user_team"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    team_id: Mapped[str] = mapped_column(String(36), index=True)
    opted_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def is_memory_opted_out(session: Session, *, user_id: str, team_id: str) -> bool:
    row = session.scalar(
        select(MemoryOptOutRow).where(
            MemoryOptOutRow.user_id == user_id,
            MemoryOptOutRow.team_id == team_id,
        )
    )
    return row is not None


def get_memory_opt_out(session: Session, *, user_id: str, team_id: str) -> MemoryOptOutRow | None:
    return session.scalar(
        select(MemoryOptOutRow).where(
            MemoryOptOutRow.user_id == user_id,
            MemoryOptOutRow.team_id == team_id,
        )
    )


def set_memory_opt_out(session: Session, *, user_id: str, team_id: str) -> MemoryOptOutRow:
    existing = get_memory_opt_out(session, user_id=user_id, team_id=team_id)
    if existing is not None:
        return existing
    now = _utcnow()
    row = MemoryOptOutRow(user_id=user_id, team_id=team_id, opted_out_at=now, created_at=now)
    session.add(row)
    session.flush()
    return row


def clear_memory_opt_out(session: Session, *, user_id: str, team_id: str) -> bool:
    row = get_memory_opt_out(session, user_id=user_id, team_id=team_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
