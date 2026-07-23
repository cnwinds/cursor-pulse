"""DEPRECATED: DingTalk text path now uses AgentRuntime. Kept temporarily for rollback reference."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from assistant_platform.conversation.models import ChatSessionRow

_PENDING_KEY = "pending_capability"
_DEFAULT_TTL_SECONDS = 300


def set_pending_capability(
    session_row: ChatSessionRow,
    *,
    capability_key: str,
    arguments: dict[str, Any],
    display_name: str | None = None,
) -> None:
    state = dict(session_row.session_state_json or {})
    state[_PENDING_KEY] = {
        "capability_key": capability_key,
        "arguments": arguments,
        "display_name": display_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    session_row.session_state_json = state


def clear_pending_capability(session_row: ChatSessionRow) -> None:
    state = dict(session_row.session_state_json or {})
    state.pop(_PENDING_KEY, None)
    session_row.session_state_json = state


def get_pending_capability(
    session_row: ChatSessionRow,
    *,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any] | None:
    state = session_row.session_state_json or {}
    pending = state.get(_PENDING_KEY)
    if not pending:
        return None
    created_raw = pending.get("created_at")
    if created_raw:
        created = datetime.fromisoformat(created_raw)
        if datetime.now(timezone.utc) - created > timedelta(seconds=ttl_seconds):
            clear_pending_capability(session_row)
            return None
    return pending
