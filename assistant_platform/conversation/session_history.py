from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow

_HISTORY_ROLES = frozenset({"user", "assistant"})
_EXCLUDED_ASSISTANT_KINDS = frozenset({"thinking", "context"})


def load_session_history_messages(
    db_session: Session,
    *,
    session_id: str,
    limit: int,
    exclude_message_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load texts for ONE session only (caller must pass the actor's open session)."""
    stmt = (
        select(ChatMessageRow)
        .where(
            ChatMessageRow.session_id == session_id,
            ChatMessageRow.role.in_(tuple(_HISTORY_ROLES)),
        )
        .order_by(ChatMessageRow.created_at.asc())
    )
    rows = list(db_session.scalars(stmt))
    if exclude_message_id:
        rows = [r for r in rows if r.id != exclude_message_id]
    filtered: list[ChatMessageRow] = []
    for row in rows:
        if row.role == "assistant":
            meta = row.meta_json or {}
            kind = str(meta.get("kind") or "").strip().lower()
            if kind in _EXCLUDED_ASSISTANT_KINDS or meta.get("ledger_only"):
                continue
        if not (row.text_redacted or "").strip():
            continue
        filtered.append(row)
    rows = filtered
    if limit > 0 and len(rows) > limit:
        rows = rows[-limit:]
    return [{"role": row.role, "content": row.text_redacted or ""} for row in rows]
