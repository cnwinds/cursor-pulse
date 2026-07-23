from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.storage.models import BackgroundJobRow
from assistant_platform.storage.repository import AssistantRepository

_TURN_KEY = "turn"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _state(session_row: ChatSessionRow) -> dict[str, Any]:
    return dict(session_row.session_state_json or {})


def _save_state(session_row: ChatSessionRow, state: dict[str, Any]) -> None:
    session_row.session_state_json = state
    flag_modified(session_row, "session_state_json")


def is_turn_running(session_row: ChatSessionRow) -> bool:
    turn = _state(session_row).get(_TURN_KEY) or {}
    return turn.get("status") == "running"


def turn_started_at(session_row: ChatSessionRow) -> datetime | None:
    turn = _state(session_row).get(_TURN_KEY) or {}
    raw = turn.get("started_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _mark_message_handled(db_session: Session, message_id: str) -> None:
    row = db_session.get(ChatMessageRow, message_id)
    if row is None:
        return
    row.handled_at = _utcnow()
    db_session.add(row)


def list_pending_user_messages(
    db_session: Session,
    session_id: str,
    *,
    limit: int | None = None,
) -> list[ChatMessageRow]:
    """User messages not yet consumed by the agent (handled_at IS NULL)."""
    stmt = (
        select(ChatMessageRow)
        .where(
            ChatMessageRow.session_id == session_id,
            ChatMessageRow.role == "user",
            ChatMessageRow.handled_at.is_(None),
        )
        .order_by(ChatMessageRow.created_at.asc())
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db_session.scalars(stmt))


def begin_turn(
    db_session: Session,
    session_row: ChatSessionRow,
    *,
    trigger_message_id: str,
) -> None:
    state = _state(session_row)
    state.pop("inbox", None)  # legacy cleanup
    state[_TURN_KEY] = {
        "status": "running",
        "trigger_message_id": trigger_message_id,
        "started_at": _utcnow_iso(),
    }
    _save_state(session_row, state)
    _mark_message_handled(db_session, trigger_message_id)


def end_turn(db_session: Session, session_row: ChatSessionRow) -> list[dict[str, Any]]:
    state = _state(session_row)
    turn = dict(state.get(_TURN_KEY) or {})
    turn["status"] = "idle"
    turn.pop("started_at", None)
    state[_TURN_KEY] = turn
    state.pop("inbox", None)
    _save_state(session_row, state)

    pending = list_pending_user_messages(db_session, session_row.id)
    return [
        {
            "message_id": row.id,
            "text": row.text_redacted or "",
            "received_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in pending
    ]


def _session_has_active_process_job(db_session: Session, session_id: str) -> bool:
    jobs = db_session.scalars(
        select(BackgroundJobRow).where(
            BackgroundJobRow.job_type == "session.process",
            BackgroundJobRow.status.in_(["queued", "processing"]),
        )
    ).all()
    for job in jobs:
        if str(job.payload_json.get("session_id") or "") == session_id:
            return True
    return False


def try_schedule_next_turn(
    db_session: Session,
    session_row: ChatSessionRow,
    repo: AssistantRepository,
) -> bool:
    """Begin a turn and enqueue session.process when idle and unhandled messages exist."""
    if session_row.status != "open":
        return False
    if is_turn_running(session_row):
        return False
    if _session_has_active_process_job(db_session, session_row.id):
        return False
    pending = list_pending_user_messages(db_session, session_row.id, limit=1)
    if not pending:
        return False
    message = pending[0]
    begin_turn(db_session, session_row, trigger_message_id=message.id)
    db_session.add(session_row)
    db_session.flush()
    repo.add_job(
        job_type="session.process",
        payload={
            "incoming_event_id": message.incoming_event_id,
            "session_id": session_row.id,
            "message_id": message.id,
        },
    )
    return True


def reschedule_session_after_turn(
    session_factory,
    session_id: str,
) -> bool:
    """Post-commit barrier: pick up messages that landed while the turn was ending."""
    db_session = session_factory()
    try:
        session_row = db_session.get(ChatSessionRow, session_id)
        if session_row is None:
            return False
        repo = AssistantRepository(db_session)
        scheduled = try_schedule_next_turn(db_session, session_row, repo)
        if scheduled:
            db_session.commit()
        return scheduled
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()


@dataclass(frozen=True)
class InboxEntry:
    message_id: str
    text: str
    received_at: str


class TurnInbox:
    """Poll pending user messages from ap_chat_messages (not session_state JSON)."""

    def __init__(
        self,
        db_session: Session,
        session_row: ChatSessionRow,
        *,
        max_per_drain: int = 5,
    ) -> None:
        self._db = db_session
        self._session_row = session_row
        self._max_per_drain = max(1, max_per_drain)

    def drain_unconsumed(self) -> list[InboxEntry]:
        rows = list_pending_user_messages(
            self._db,
            self._session_row.id,
            limit=self._max_per_drain,
        )
        return [
            InboxEntry(
                message_id=row.id,
                text=row.text_redacted or "",
                received_at=row.created_at.isoformat() if row.created_at else "",
            )
            for row in rows
        ]

    def mark_consumed(self, message_id: str) -> None:
        _mark_message_handled(self._db, message_id)
        self._db.commit()
