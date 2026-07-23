from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.review.models import HumanReviewRow, SessionReviewRow

_HUMAN_QUEUE_THRESHOLD = 60


def _message_has_tool_failure(message: ChatMessageRow) -> bool:
    meta = message.meta_json or {}
    tags = meta.get("tags") or []
    if "tool_failed" in tags:
        return True
    if meta.get("tool_status") == "failed":
        return True
    return False


def run_auto_review(db_session: Session, session_id: str) -> SessionReviewRow:
    session_row = db_session.get(ChatSessionRow, session_id)
    if session_row is None:
        raise ValueError(f"session not found: {session_id}")

    existing = db_session.scalar(
        select(SessionReviewRow).where(SessionReviewRow.session_id == session_id)
    )
    if existing is not None:
        return existing

    messages = list(
        db_session.scalars(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_id)
            .order_by(ChatMessageRow.created_at.asc())
        )
    )

    score = 80
    failure_tags: list[str] = []
    evidence: dict = {"message_count": len(messages)}

    error_messages = [m for m in messages if m.role == "error"]
    if error_messages:
        score -= 20
        failure_tags.append("error_messages")
        evidence["error_count"] = len(error_messages)

    assistant_messages = [m for m in messages if m.role == "assistant"]
    if not assistant_messages or all(not (m.text_redacted or "").strip() for m in assistant_messages):
        score -= 10
        failure_tags.append("empty_assistant_reply")
        evidence["assistant_message_count"] = len(assistant_messages)

    tool_failures = [m for m in messages if _message_has_tool_failure(m)]
    if tool_failures:
        score -= 15
        failure_tags.append("tool_failed")
        evidence["tool_failure_count"] = len(tool_failures)

    score = max(0, min(100, score))

    review = SessionReviewRow(
        session_id=session_id,
        status="completed",
        score=score,
        failure_tags_json=failure_tags,
        evidence_json=evidence,
    )
    db_session.add(review)
    db_session.flush()

    if score < _HUMAN_QUEUE_THRESHOLD:
        db_session.add(
            HumanReviewRow(
                session_id=session_id,
                reviewer=None,
                notes="auto_queued",
            )
        )
        db_session.flush()

    return review
