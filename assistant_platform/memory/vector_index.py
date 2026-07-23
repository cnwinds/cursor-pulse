"""Local vector index adapter for archive chunks.

Stores embeddings on ``ArchiveChunkRow.embedding_json`` and searches in-process.
The Protocol boundary is intentionally thin so Phase 4 hybrid recall can swap in
pgvector / OpenSearch without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.memory.archive_models import ArchiveChunkRow
from assistant_platform.memory.embedding import Embedder, HashingEmbedder, cosine_similarity


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    session_id: str
    team_id: str
    subject_id: str
    scope: str
    text: str
    content_hash: str
    occurred_at: datetime
    embedding_model: str | None = None


@dataclass(frozen=True)
class VectorHit:
    chunk_id: str
    session_id: str
    team_id: str
    subject_id: str
    scope: str
    score: float
    text: str


class VectorIndex(Protocol):
    """Stable interface for local and future remote vector backends."""

    def upsert(self, records: list[VectorRecord]) -> None: ...

    def delete_by_session(self, session_id: str) -> None: ...

    def search(
        self,
        query: str,
        *,
        team_id: str,
        subject_id: str,
        scope: str,
        top_k: int = 10,
    ) -> list[VectorHit]: ...


class LocalVectorIndex:
    """SQLite JSON embedding store + cosine search (no external vector DB)."""

    def __init__(
        self,
        session: Session,
        *,
        embedder: Embedder | None = None,
        embedding_model: str = "hashing-embedder",
    ):
        self._session = session
        self._embedder = embedder or HashingEmbedder()
        self._embedding_model = embedding_model

    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        by_hash: dict[str, list[float]] = {}
        for record in records:
            row = self._session.get(ArchiveChunkRow, record.chunk_id)
            if row is None:
                # Allow callers to upsert metadata onto existing chunks only.
                continue
            if record.content_hash and record.content_hash in by_hash:
                vec = by_hash[record.content_hash]
            else:
                vec = self._embedder.embed(record.text)
                if record.content_hash:
                    by_hash[record.content_hash] = vec
            row.embedding_json = list(vec)
            row.embedding_model = record.embedding_model or self._embedding_model
            row.indexed_at = record.occurred_at
            self._session.add(row)

    def delete_by_session(self, session_id: str) -> None:
        rows = self._session.scalars(
            select(ArchiveChunkRow).where(ArchiveChunkRow.session_id == session_id)
        ).all()
        for row in rows:
            row.embedding_json = None
            row.embedding_model = None
            row.indexed_at = None
            self._session.add(row)

    def search(
        self,
        query: str,
        *,
        team_id: str,
        subject_id: str,
        scope: str,
        top_k: int = 10,
    ) -> list[VectorHit]:
        query_vec = self._embedder.embed(query)
        if not query_vec:
            return []
        rows = self._session.scalars(
            select(ArchiveChunkRow).where(
                ArchiveChunkRow.team_id == team_id,
                ArchiveChunkRow.subject_id == subject_id,
                ArchiveChunkRow.scope == scope,
                ArchiveChunkRow.embedding_json.is_not(None),
            )
        ).all()
        scored: list[VectorHit] = []
        for row in rows:
            if not row.embedding_json:
                continue
            score = cosine_similarity(query_vec, list(row.embedding_json))
            scored.append(
                VectorHit(
                    chunk_id=row.id,
                    session_id=row.session_id,
                    team_id=row.team_id,
                    subject_id=row.subject_id,
                    scope=row.scope,
                    score=score,
                    text=row.text,
                )
            )
        scored.sort(key=lambda hit: (-hit.score, hit.chunk_id))
        return scored[: max(0, top_k)]
