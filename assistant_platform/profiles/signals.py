from __future__ import annotations

from assistant_platform.profiles.extractor import (
    ExtractedProfileSignal,
    extract_profile_signals_from_session,
    extract_signals_from_summary,
    persist_profile_signals,
)

__all__ = [
    "ExtractedProfileSignal",
    "create_profile_signal_from_session",
    "extract_profile_signals_from_session",
    "extract_signals_from_summary",
    "persist_profile_signals",
]


def create_profile_signal_from_session(session, session_row):
    """Backward-compatible shim; prefer archive pipeline profile stage."""
    from assistant_platform.memory.session_summary import build_session_summary_from_archive
    from assistant_platform.memory.archive_models import ArchiveChunkRow, ArchiveMessageRow
    from sqlalchemy import select

    if session_row.conversation_type == "group" or not session_row.user_id:
        return None
    messages = list(
        session.scalars(
            select(ArchiveMessageRow)
            .where(ArchiveMessageRow.session_id == session_row.id)
            .order_by(ArchiveMessageRow.seq.asc())
        ).all()
    )
    if not messages:
        from assistant_platform.conversation.models import ChatMessageRow

        chat_messages = list(
            session.scalars(
                select(ChatMessageRow)
                .where(ChatMessageRow.session_id == session_row.id)
                .order_by(ChatMessageRow.created_at.asc())
            ).all()
        )
        if not chat_messages:
            return None
        from datetime import datetime, timezone

        messages = [
            ArchiveMessageRow(
                session_id=session_row.id,
                seq=idx,
                role=m.role,
                text_redacted=m.text_redacted or "",
                created_at=m.created_at or datetime.now(timezone.utc),
            )
            for idx, m in enumerate(chat_messages, start=1)
        ]
    chunks = list(
        session.scalars(
            select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_row.id)
        ).all()
    )
    summary = build_session_summary_from_archive(session_row, messages, chunks)
    rows = extract_profile_signals_from_session(session, session_row, summary)
    return rows[0] if rows else None
