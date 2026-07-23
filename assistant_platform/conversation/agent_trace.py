from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.secrets.redact import redact_text

_MAX_RESULT_CHARS = 24_000


def redact_tool_arguments(raw_arguments: str | dict[str, Any] | None) -> dict[str, Any] | str:
    """Parse tool arguments when possible; strip secret fields; redact key-like strings."""
    if raw_arguments is None:
        return {}
    if isinstance(raw_arguments, dict):
        data: Any = dict(raw_arguments)
    else:
        text = str(raw_arguments)
        try:
            parsed = json.loads(text or "{}")
        except json.JSONDecodeError:
            redacted, _refs = redact_text(text)
            return redacted
        data = parsed
    if not isinstance(data, dict):
        redacted, _refs = redact_text(json.dumps(data, ensure_ascii=False, default=str))
        return redacted
    cleaned = dict(data)
    for key in ("api_key", "apiKey", "token", "secret", "password"):
        cleaned.pop(key, None)
    dumped = json.dumps(cleaned, ensure_ascii=False, default=str)
    redacted, _refs = redact_text(dumped)
    try:
        return json.loads(redacted)
    except json.JSONDecodeError:
        return redacted


def redact_tool_result(raw_result: str | None) -> str:
    text = raw_result or ""
    redacted, _refs = redact_text(text)
    if len(redacted) > _MAX_RESULT_CHARS:
        return redacted[:_MAX_RESULT_CHARS] + "\n…(truncated)"
    return redacted


def persist_agent_trace_event(
    db_session: Session,
    *,
    session_row: ChatSessionRow,
    event: dict[str, Any],
    commit: bool = True,
) -> ChatMessageRow | None:
    """Persist one agent observability event into the session ledger."""
    event_type = str(event.get("type") or "").strip()
    if event_type == "thinking":
        text = str(event.get("text") or "").strip()
        if not text:
            return None
        if event.get("delivered_as_interim"):
            # Already visible as interim assistant bubble; avoid duplicate.
            return None
        row = ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted=text,
            secret_refs_json=[],
            meta_json={
                "kind": "thinking",
                "round": event.get("round"),
                "ledger_only": True,
            },
        )
    elif event_type == "tool":
        name = str(event.get("name") or "").strip() or "unknown"
        arguments = redact_tool_arguments(event.get("arguments"))
        result_text = redact_tool_result(
            event.get("result") if isinstance(event.get("result"), str) else None
        )
        if result_text is None and event.get("result") is not None:
            result_text = redact_tool_result(
                json.dumps(event.get("result"), ensure_ascii=False, default=str)
            )
        row = ChatMessageRow(
            session_id=session_row.id,
            role="tool",
            text_redacted=result_text or "",
            secret_refs_json=[],
            meta_json={
                "kind": "tool",
                "name": name,
                "tool_call_id": str(event.get("tool_call_id") or ""),
                "arguments": arguments,
                "round": event.get("round"),
            },
        )
    elif event_type == "context":
        skills = event.get("skills") if isinstance(event.get("skills"), list) else []
        tools = event.get("tools") if isinstance(event.get("tools"), list) else []
        skill_ids = [
            str(item.get("skill_id") or item.get("name") or "").strip()
            for item in skills
            if isinstance(item, dict)
        ]
        tool_names = [
            str(item.get("name") or item.get("capability_key") or "").strip()
            for item in tools
            if isinstance(item, dict)
        ]
        skill_ids = [s for s in skill_ids if s]
        tool_names = [t for t in tool_names if t]
        summary = (
            f"技能 {len(skill_ids)} · 工具 {len(tool_names)}"
            if (skill_ids or tool_names)
            else "本轮未注入技能/工具名片"
        )
        row = ChatMessageRow(
            session_id=session_row.id,
            role="assistant",
            text_redacted=summary,
            secret_refs_json=[],
            meta_json={
                "kind": "context",
                "skills": skills,
                "tools": tools,
                "ledger_only": True,
            },
        )
    else:
        return None

    db_session.add(row)
    db_session.flush()
    if commit:
        db_session.commit()
    return row
