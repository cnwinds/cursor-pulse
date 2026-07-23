"""Archive + FTS/vector indexer for closed chat sessions."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_models import (
    ArchiveChunkRow,
    ArchiveMessageRow,
    SessionArchiveRow,
    resolve_archive_scope,
)
from assistant_platform.memory.vector_index import LocalVectorIndex, VectorIndex, VectorRecord
from assistant_platform.memory.embedding import Embedder, HashingEmbedder

logger = logging.getLogger(__name__)

_INDEXABLE_ASSISTANT_KINDS = frozenset({"final", ""})


@dataclass(frozen=True)
class PreparedChunk:
    chunk_index: int
    start_seq: int
    end_seq: int
    text: str
    source_roles: tuple[str, ...]
    source_message_ids: tuple[str, ...]
    occurred_from: datetime
    occurred_to: datetime
    token_count: int
    content_hash: str


def estimate_tokens(text: str) -> int:
    """Lightweight token estimate (whitespace + CJK-aware) for chunk budgets."""
    stripped = (text or "").strip()
    if not stripped:
        return 0
    # Approx: Latin words + each CJK char counts as one token.
    tokens = 0
    buf: list[str] = []
    for ch in stripped:
        if "\u4e00" <= ch <= "\u9fff":
            if buf:
                tokens += 1
                buf.clear()
            tokens += 1
        elif ch.isspace():
            if buf:
                tokens += 1
                buf.clear()
        else:
            buf.append(ch)
    if buf:
        tokens += 1
    return max(1, tokens)


def content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def is_indexable_message(message: ChatMessageRow | ArchiveMessageRow) -> bool:
    role = (message.role or "").lower()
    if role == "user":
        return True
    if role != "assistant":
        return False
    meta = getattr(message, "meta_json", None) or {}
    kind = str(meta.get("kind") or "").strip().lower()
    if kind == "interim":
        return False
    return kind in _INDEXABLE_ASSISTANT_KINDS or kind == ""


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _message_sort_key(message: ChatMessageRow) -> tuple:
    created = message.created_at or datetime.min.replace(tzinfo=timezone.utc)
    return (_ensure_aware(created), message.id or "")


def _split_text_windows(text: str, *, max_tokens: int, overlap_tokens: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    if estimate_tokens(text) <= max_tokens:
        return [text]
    windows: list[str] = []
    start = 0
    while start < len(words):
        end = start
        token_count = 0
        while end < len(words):
            piece = " ".join(words[start : end + 1])
            next_tokens = estimate_tokens(piece)
            if next_tokens > max_tokens and end > start:
                break
            token_count = next_tokens
            end += 1
            if token_count >= max_tokens:
                break
        windows.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        overlap = max(0, min(overlap_tokens, end - start - 1))
        start = max(start + 1, end - overlap)
    return windows or [text]


def build_indexable_chunks(
    messages: Sequence[ChatMessageRow | ArchiveMessageRow],
    *,
    max_tokens_per_chunk: int = 512,
    overlap_tokens: int = 64,
) -> list[PreparedChunk]:
    """Build retrieval chunks from ordered messages (1-based seq by list order)."""
    indexed: list[tuple[int, ChatMessageRow | ArchiveMessageRow]] = []
    for seq, message in enumerate(messages, start=1):
        if is_indexable_message(message):
            indexed.append((seq, message))

    turns: list[list[tuple[int, ChatMessageRow | ArchiveMessageRow]]] = []
    current: list[tuple[int, ChatMessageRow | ArchiveMessageRow]] = []
    pending_assistants: list[tuple[int, ChatMessageRow | ArchiveMessageRow]] = []
    for seq, message in indexed:
        role = (message.role or "").lower()
        if role == "user":
            if current:
                turns.append(current)
            current = [*pending_assistants, (seq, message)]
            pending_assistants = []
        else:
            if current:
                current.append((seq, message))
            else:
                # Leading assistant replies (same-timestamp reorder) wait for a user.
                pending_assistants.append((seq, message))
    if current:
        if pending_assistants:
            current.extend(pending_assistants)
        turns.append(current)
    elif pending_assistants:
        turns.append(pending_assistants)

    prepared: list[PreparedChunk] = []
    chunk_index = 0
    for turn in turns:
        parts: list[str] = []
        roles: list[str] = []
        source_ids: list[str] = []
        times: list[datetime] = []
        for seq, message in turn:
            text = (message.text_redacted or "").strip()
            if not text:
                continue
            parts.append(f"{message.role}: {text}")
            roles.append(message.role)
            mid = getattr(message, "id", None) or getattr(message, "source_message_id", None) or ""
            if mid:
                source_ids.append(str(mid))
            if message.created_at is not None:
                times.append(message.created_at)
        if not parts:
            continue
        combined = "\n".join(parts)
        start_seq = min(seq for seq, _ in turn)
        end_seq = max(seq for seq, _ in turn)
        occurred_from = min(times) if times else datetime.now(timezone.utc)
        occurred_to = max(times) if times else occurred_from
        windows = _split_text_windows(
            combined,
            max_tokens=max(1, max_tokens_per_chunk),
            overlap_tokens=max(0, overlap_tokens),
        )
        for window in windows:
            prepared.append(
                PreparedChunk(
                    chunk_index=chunk_index,
                    start_seq=start_seq,
                    end_seq=end_seq,
                    text=window,
                    source_roles=tuple(roles),
                    source_message_ids=tuple(source_ids),
                    occurred_from=occurred_from,
                    occurred_to=occurred_to,
                    token_count=estimate_tokens(window),
                    content_hash=content_hash(window),
                )
            )
            chunk_index += 1
    return prepared


def purge_session_index(session: Session, session_id: str) -> None:
    """Remove archive messages, chunks, FTS entries and vector embeddings for a session."""
    chunk_ids = session.scalars(
        select(ArchiveChunkRow.id).where(ArchiveChunkRow.session_id == session_id)
    ).all()
    if chunk_ids:
        for chunk_id in chunk_ids:
            session.execute(
                text("DELETE FROM ap_archive_chunks_fts WHERE chunk_id = :cid"),
                {"cid": chunk_id},
            )
    session.execute(delete(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_id))
    session.execute(delete(ArchiveMessageRow).where(ArchiveMessageRow.session_id == session_id))


def _sync_fts_chunk(session: Session, chunk: ArchiveChunkRow) -> None:
    session.execute(
        text("DELETE FROM ap_archive_chunks_fts WHERE chunk_id = :cid"),
        {"cid": chunk.id},
    )
    session.execute(
        text(
            "INSERT INTO ap_archive_chunks_fts"
            "(chunk_id, session_id, team_id, subject_id, scope, text) "
            "VALUES (:chunk_id, :session_id, :team_id, :subject_id, :scope, :text)"
        ),
        {
            "chunk_id": chunk.id,
            "session_id": chunk.session_id,
            "team_id": chunk.team_id,
            "subject_id": chunk.subject_id,
            "scope": chunk.scope,
            "text": chunk.text,
        },
    )


def _get_or_create_archive_header(
    session: Session,
    session_row: ChatSessionRow,
    *,
    index_version: int,
) -> SessionArchiveRow:
    scope, subject_id = resolve_archive_scope(
        conversation_type=session_row.conversation_type,
        user_id=session_row.user_id,
        conversation_id=session_row.conversation_id,
    )
    row = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = SessionArchiveRow(
            session_id=session_row.id,
            team_id=session_row.team_id,
            scope=scope.value,
            subject_id=subject_id,
            assistant_id=session_row.assistant_id,
            channel=session_row.channel,
            conversation_type=session_row.conversation_type,
            conversation_id=session_row.conversation_id,
            user_id=session_row.user_id,
            status="pending",
            archive_status="pending",
            index_status="pending",
            index_version=index_version,
        )
        session.add(row)
    else:
        row.scope = scope.value
        row.subject_id = subject_id
        row.team_id = session_row.team_id
        row.index_version = index_version
        row.updated_at = now
        row.last_error = None
    session.flush()
    return row


def archive_session_messages(
    session: Session,
    session_row: ChatSessionRow,
    *,
    index_version: int = 2,
) -> SessionArchiveRow:
    """Copy ledger messages into permanent archive (idempotent on content hash)."""
    archive = _get_or_create_archive_header(session, session_row, index_version=index_version)
    now = datetime.now(timezone.utc)
    messages = list(
        session.scalars(
            select(ChatMessageRow)
            .where(ChatMessageRow.session_id == session_row.id)
            .order_by(ChatMessageRow.created_at.asc(), ChatMessageRow.id.asc())
        ).all()
    )
    messages.sort(key=_message_sort_key)
    hashes: list[str] = []
    for message in messages:
        hashes.append(content_hash(message.text_redacted or ""))
    digest = content_hash("|".join(hashes))
    if archive.archive_status == "ready" and archive.content_hash == digest:
        return archive

    purge_session_index(session, session_row.id)
    session.flush()
    archive.archive_status = "partial"
    archive.status = "partial"
    archive.index_status = "pending"
    session.flush()

    for seq, message in enumerate(messages, start=1):
        digest_msg = content_hash(message.text_redacted or "")
        session.add(
            ArchiveMessageRow(
                session_id=session_row.id,
                seq=seq,
                source_message_id=message.id,
                role=message.role,
                text_redacted=message.text_redacted or "",
                content_hash=digest_msg,
                meta_json=dict(message.meta_json or {}),
                created_at=message.created_at or now,
            )
        )
    session.flush()

    archive.message_total = len(messages)
    archive.content_hash = digest
    if messages:
        archive.occurred_from = messages[0].created_at
        archive.occurred_to = messages[-1].created_at
    archive.archive_status = "ready"
    archive.archived_at = now
    archive.updated_at = now
    session.flush()
    return archive


def index_archived_session(
    session: Session,
    session_row: ChatSessionRow,
    *,
    index_version: int = 2,
    max_tokens_per_chunk: int = 512,
    overlap_tokens: int = 64,
    vector_index: VectorIndex | None = None,
    embedder: Embedder | None = None,
    embedding_enabled: bool = True,
    embedding_model: str = "hashing-embedder",
) -> SessionArchiveRow:
    """Build retrieval chunks + FTS/vector indexes from archived messages."""
    archive = _get_or_create_archive_header(session, session_row, index_version=index_version)
    if archive.archive_status != "ready":
        raise RuntimeError(f"archive not ready for session {session_row.id}")

    now = datetime.now(timezone.utc)
    messages = list(
        session.scalars(
            select(ArchiveMessageRow)
            .where(ArchiveMessageRow.session_id == session_row.id)
            .order_by(ArchiveMessageRow.seq.asc())
        ).all()
    )
    prepared = build_indexable_chunks(
        messages,
        max_tokens_per_chunk=max_tokens_per_chunk,
        overlap_tokens=overlap_tokens,
    )
    existing_chunks = session.scalars(
        select(ArchiveChunkRow).where(
            ArchiveChunkRow.session_id == session_row.id,
            ArchiveChunkRow.index_version == index_version,
        )
    ).all()
    if archive.index_status == "ready" and len(existing_chunks) == len(prepared) and prepared:
        return archive

    chunk_ids = session.scalars(
        select(ArchiveChunkRow.id).where(ArchiveChunkRow.session_id == session_row.id)
    ).all()
    if chunk_ids:
        for chunk_id in chunk_ids:
            session.execute(
                text("DELETE FROM ap_archive_chunks_fts WHERE chunk_id = :cid"),
                {"cid": chunk_id},
            )
    session.execute(delete(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_row.id))
    session.flush()

    chunk_rows: list[ArchiveChunkRow] = []
    for item in prepared:
        chunk = ArchiveChunkRow(
            id=str(uuid.uuid4()),
            session_id=session_row.id,
            team_id=archive.team_id,
            scope=archive.scope,
            subject_id=archive.subject_id,
            chunk_index=item.chunk_index,
            start_seq=item.start_seq,
            end_seq=item.end_seq,
            text=item.text,
            content_hash=item.content_hash,
            source_roles_json=list(item.source_roles),
            source_message_ids_json=list(item.source_message_ids),
            occurred_from=item.occurred_from,
            occurred_to=item.occurred_to,
            index_version=index_version,
            token_count=item.token_count,
            indexed_at=now,
        )
        session.add(chunk)
        chunk_rows.append(chunk)
    session.flush()

    for chunk in chunk_rows:
        _sync_fts_chunk(session, chunk)

    if embedding_enabled and chunk_rows:
        index = vector_index or LocalVectorIndex(
            session,
            embedder=embedder or HashingEmbedder(),
            embedding_model=embedding_model,
        )
        index.upsert(
            [
                VectorRecord(
                    chunk_id=chunk.id,
                    session_id=chunk.session_id,
                    team_id=chunk.team_id,
                    subject_id=chunk.subject_id,
                    scope=chunk.scope,
                    text=chunk.text,
                    content_hash=chunk.content_hash,
                    occurred_at=now,
                )
                for chunk in chunk_rows
            ]
        )

    archive.chunk_total = len(chunk_rows)
    archive.index_status = "ready"
    archive.indexed_at = now
    archive.status = "ready"
    archive.updated_at = now
    session.flush()
    return archive


def archive_and_index_session(
    session: Session,
    session_row: ChatSessionRow,
    *,
    index_version: int = 2,
    max_tokens_per_chunk: int = 512,
    overlap_tokens: int = 64,
    vector_index: VectorIndex | None = None,
    embedder: Embedder | None = None,
    embedding_enabled: bool = True,
    embedding_model: str = "hashing-embedder",
) -> SessionArchiveRow:
    """Copy ledger messages into permanent archive and rebuild search indexes.

    Idempotent for the same ``index_version``: replaces prior archive/index rows
    for the session inside the caller's transaction.
    """
    archive = _get_or_create_archive_header(session, session_row, index_version=index_version)
    now = datetime.now(timezone.utc)
    try:
        archive_session_messages(session, session_row, index_version=index_version)
        index_archived_session(
            session,
            session_row,
            index_version=index_version,
            max_tokens_per_chunk=max_tokens_per_chunk,
            overlap_tokens=overlap_tokens,
            vector_index=vector_index,
            embedder=embedder,
            embedding_enabled=embedding_enabled,
            embedding_model=embedding_model,
        )
        return session.scalar(
            select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_row.id)
        )
    except Exception as exc:
        archive.status = "failed"
        archive.archive_status = (
            "failed" if archive.archive_status != "ready" else archive.archive_status
        )
        archive.index_status = "failed"
        archive.last_error = str(exc)[:500]
        archive.updated_at = now
        session.flush()
        logger.exception("archive_and_index_session failed for %s", session_row.id)
        raise
