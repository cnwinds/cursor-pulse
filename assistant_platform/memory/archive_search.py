"""Hybrid archive recall: scope filter, FTS + vector + facts fusion, expand/read."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from assistant_platform.config import AssistantChatMemoryConfig, MemoryRecallBudgetConfig
from assistant_platform.memory.embedder import build_archive_embedder
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.memory.archive_models import (
    ArchiveChunkRow,
    ArchiveMessageRow,
    SessionArchiveRow,
    resolve_archive_scope,
)
from assistant_platform.memory.contracts import (
    ArchiveHit,
    ChunkAnchor,
    FactRecallItem,
    MemoryScope,
    MemorySourceType,
    NeighborWindow,
    RecallCursor,
    SearchPageMeta,
)
from assistant_platform.memory.vector_index import LocalVectorIndex, VectorHit
from assistant_platform.memory.semantic.domain import AtomKind, VisibilityContext, team_id_to_namespace
from assistant_platform.memory.semantic.recall import recall_memories
from assistant_platform.memory.semantic.repository import SemanticMemoryRepository
from assistant_platform.memory.embedding import Embedder

logger = logging.getLogger(__name__)

_DELETED_STATUSES = frozenset({"deleted"})


class RecallTimeoutError(TimeoutError):
    """Hybrid recall candidate fetch exceeded configured ``recall.timeout_ms``."""


@dataclass(frozen=True)
class SearchScope:
    team_id: str
    subject_id: str
    scope: MemoryScope
    conversation_type: str = "private"
    exclude_session_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class _ScoredChunk:
    chunk_id: str
    fts_score: float = 0.0
    vector_score: float = 0.0
    fused_score: float = 0.0


def resolve_search_scope(
    *,
    team_id: str,
    subject_id: str,
    conversation_type: str,
    conversation_id: str,
    user_id: str | None = None,
    exclude_session_id: str | None = None,
) -> SearchScope:
    scope, archive_subject = resolve_archive_scope(
        conversation_type=conversation_type,
        user_id=user_id or subject_id,
        conversation_id=conversation_id,
    )
    exclude: set[str] = set()
    if exclude_session_id:
        exclude.add(exclude_session_id)
    return SearchScope(
        team_id=team_id,
        subject_id=archive_subject,
        scope=scope,
        conversation_type=conversation_type,
        exclude_session_ids=frozenset(exclude),
    )


def compute_query_fingerprint(query: str, scope: SearchScope) -> str:
    raw = "|".join(
        [
            (query or "").strip().lower(),
            scope.team_id,
            scope.subject_id,
            scope.scope.value,
            ",".join(sorted(scope.exclude_session_ids)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _escape_fts_term(term: str) -> str:
    cleaned = re.sub(r'["\'\\]', " ", term or "").strip()
    if not cleaned:
        return ""
    return cleaned.replace('"', '""')


def _fts_query_text(query: str) -> str:
    """Build an FTS5 MATCH expression tuned for the trigram tokenizer."""
    cleaned = (query or "").strip()
    if not cleaned:
        return ""
    # Trigram supports substring match; pass CJK / no-space queries as one phrase.
    if not re.search(r"\s", cleaned):
        term = _escape_fts_term(cleaned)
        return f'"{term}"' if term else ""
    tokens = [t for t in re.split(r"\s+", cleaned) if t]
    parts = [f'"{_escape_fts_term(token)}"' for token in tokens if _escape_fts_term(token)]
    return " OR ".join(parts)


def _eligible_session_ids(session: Session, scope: SearchScope) -> set[str]:
    stmt = select(SessionArchiveRow.session_id).where(
        SessionArchiveRow.team_id == scope.team_id,
        SessionArchiveRow.subject_id == scope.subject_id,
        SessionArchiveRow.scope == scope.scope.value,
        SessionArchiveRow.index_status == "ready",
    )
    if scope.exclude_session_ids:
        stmt = stmt.where(SessionArchiveRow.session_id.not_in(list(scope.exclude_session_ids)))
    archive_ids = set(session.scalars(stmt).all())
    if not archive_ids:
        return set()

    open_ids = set(
        session.scalars(
            select(ChatSessionRow.id).where(
                ChatSessionRow.id.in_(archive_ids),
                ChatSessionRow.status != "closed",
            )
        ).all()
    )
    return {sid for sid in archive_ids if sid not in open_ids and sid not in scope.exclude_session_ids}


def _session_is_in_scope(session: Session, session_id: str, scope: SearchScope) -> bool:
    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)
    )
    if archive is None:
        return False
    if archive.status in _DELETED_STATUSES or archive.index_status != "ready":
        return False
    if archive.team_id != scope.team_id:
        return False
    if archive.subject_id != scope.subject_id:
        return False
    if archive.scope != scope.scope.value:
        return False
    if session_id in scope.exclude_session_ids:
        return False
    chat = session.get(ChatSessionRow, session_id)
    if chat is not None and chat.status != "closed":
        return False
    return True


def _fts_search(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    limit: int,
) -> list[tuple[str, float]]:
    fts_query = _fts_query_text(query)
    if not fts_query:
        return []
    eligible = _eligible_session_ids(session, scope)
    if not eligible:
        return []
    params: dict = {
        "team_id": scope.team_id,
        "subject_id": scope.subject_id,
        "scope": scope.scope.value,
        "limit": max(1, limit),
        "match": fts_query,
    }
    sql = (
        "SELECT chunk_id, bm25(ap_archive_chunks_fts) AS rank_score "
        "FROM ap_archive_chunks_fts "
        "WHERE ap_archive_chunks_fts MATCH :match "
        "AND team_id = :team_id AND subject_id = :subject_id AND scope = :scope "
    )
    placeholders = ", ".join(f":sid{i}" for i in range(len(eligible)))
    sql += f"AND session_id IN ({placeholders}) "
    for i, sid in enumerate(sorted(eligible)):
        params[f"sid{i}"] = sid
    sql += "ORDER BY rank_score LIMIT :limit"
    try:
        rows = session.execute(text(sql), params).all()
    except Exception:
        logger.exception("FTS search failed")
        return []
    hits: list[tuple[str, float]] = []
    for row in rows:
        raw = float(row.rank_score or 0.0)
        score = 1.0 / (1.0 + max(0.0, raw))
        hits.append((row.chunk_id, score))
    return hits


def _vector_search(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    limit: int,
    embedder: Embedder | None = None,
    embedding_model: str = "hashing-embedder",
) -> list[VectorHit]:
    eligible = _eligible_session_ids(session, scope)
    if not eligible:
        return []
    index = LocalVectorIndex(
        session,
        embedder=embedder,
        embedding_model=embedding_model,
    )
    try:
        hits = index.search(
            query,
            team_id=scope.team_id,
            subject_id=scope.subject_id,
            scope=scope.scope.value,
            top_k=max(limit * 3, limit),
        )
    except Exception:
        logger.exception("vector search failed")
        return []
    return [hit for hit in hits if hit.session_id in eligible][:limit]


def _normalize_scores(values: dict[str, float]) -> dict[str, float]:
    if not values:
        return {}
    max_val = max(values.values())
    min_val = min(values.values())
    if max_val <= min_val:
        return {key: 1.0 for key in values}
    span = max_val - min_val
    return {key: (val - min_val) / span for key, val in values.items()}


def _fuse_chunk_scores(
    fts_hits: list[tuple[str, float]],
    vector_hits: list[VectorHit],
    *,
    fts_weight: float,
    vector_weight: float,
) -> list[_ScoredChunk]:
    fts_map = {chunk_id: score for chunk_id, score in fts_hits}
    vec_map = {hit.chunk_id: hit.score for hit in vector_hits}
    chunk_ids = set(fts_map) | set(vec_map)
    fts_norm = _normalize_scores(fts_map)
    vec_norm = _normalize_scores(vec_map)
    total_weight = max(fts_weight + vector_weight, 1e-9)
    scored: list[_ScoredChunk] = []
    for chunk_id in chunk_ids:
        fused = (
            fts_weight * fts_norm.get(chunk_id, 0.0) + vector_weight * vec_norm.get(chunk_id, 0.0)
        ) / total_weight
        scored.append(
            _ScoredChunk(
                chunk_id=chunk_id,
                fts_score=fts_map.get(chunk_id, 0.0),
                vector_score=vec_map.get(chunk_id, 0.0),
                fused_score=fused,
            )
        )
    scored.sort(key=lambda item: (-item.fused_score, item.chunk_id))
    return scored


def _chunks_overlap(a: ArchiveHit, b: ArchiveHit) -> bool:
    if a.session_id != b.session_id:
        return False
    return not (a.end_seq < b.start_seq or b.end_seq < a.start_seq)


def _dedupe_overlapping(hits: list[ArchiveHit]) -> list[ArchiveHit]:
    kept: list[ArchiveHit] = []
    for hit in hits:
        if any(_chunks_overlap(hit, existing) for existing in kept):
            continue
        kept.append(hit)
    return kept


def _apply_session_diversity(hits: list[ArchiveHit], max_per_session: int) -> list[ArchiveHit]:
    counts: dict[str, int] = {}
    selected: list[ArchiveHit] = []
    for hit in hits:
        count = counts.get(hit.session_id, 0)
        if count >= max(1, max_per_session):
            continue
        selected.append(hit)
        counts[hit.session_id] = count + 1
    return selected


def _sort_key_for_hit(hit: ArchiveHit) -> str:
    return f"{hit.score:.6f}:{hit.memory_id}"


def _load_chunk_map(session: Session, chunk_ids: list[str]) -> dict[str, ArchiveChunkRow]:
    if not chunk_ids:
        return {}
    rows = session.scalars(select(ArchiveChunkRow).where(ArchiveChunkRow.id.in_(chunk_ids))).all()
    return {row.id: row for row in rows}


def _archive_hit_from_chunk(
    chunk: ArchiveChunkRow,
    *,
    archive: SessionArchiveRow | None,
    rank: int,
    score: float,
    chunk_total: int | None = None,
) -> ArchiveHit:
    message_total = archive.message_total if archive else 0
    session_chunk_total = chunk_total if chunk_total is not None else (archive.chunk_total if archive else 0)
    has_prev = chunk.chunk_index > 0
    has_next = chunk.chunk_index + 1 < session_chunk_total
    roles = tuple(str(r) for r in (chunk.source_roles_json or []))
    return ArchiveHit(
        memory_id=chunk.id,
        session_id=chunk.session_id,
        source_type=MemorySourceType.ARCHIVE_CHUNK,
        scope=MemoryScope(chunk.scope),
        text=chunk.text,
        source_roles=roles,
        occurred_from=_ensure_aware(chunk.occurred_from),
        occurred_to=_ensure_aware(chunk.occurred_to),
        start_seq=chunk.start_seq,
        end_seq=chunk.end_seq,
        chunk_index=chunk.chunk_index,
        session_message_total=message_total,
        session_chunk_total=session_chunk_total,
        rank=rank,
        score=score,
        has_prev=has_prev,
        has_next=has_next,
        anchor=ChunkAnchor(
            session_id=chunk.session_id,
            chunk_index=chunk.chunk_index,
            start_seq=chunk.start_seq,
            end_seq=chunk.end_seq,
        ),
    )


def _chunk_totals(session: Session, session_ids: set[str]) -> dict[str, int]:
    if not session_ids:
        return {}
    rows = session.scalars(
        select(ArchiveChunkRow).where(ArchiveChunkRow.session_id.in_(session_ids))
    ).all()
    totals: dict[str, int] = {}
    for row in rows:
        totals[row.session_id] = totals.get(row.session_id, 0) + 1
    return totals


def _build_hits(
    session: Session,
    scored: list[_ScoredChunk],
    scope: SearchScope,
) -> list[ArchiveHit]:
    chunk_map = _load_chunk_map(session, [item.chunk_id for item in scored])
    session_ids = {chunk.session_id for chunk in chunk_map.values()}
    archives = {
        row.session_id: row
        for row in session.scalars(
            select(SessionArchiveRow).where(SessionArchiveRow.session_id.in_(session_ids))
        ).all()
    }
    totals = _chunk_totals(session, session_ids)
    hits: list[ArchiveHit] = []
    for idx, item in enumerate(scored, start=1):
        chunk = chunk_map.get(item.chunk_id)
        if chunk is None:
            continue
        if chunk.team_id != scope.team_id or chunk.subject_id != scope.subject_id:
            continue
        if chunk.scope != scope.scope.value:
            continue
        archive = archives.get(chunk.session_id)
        hits.append(
            _archive_hit_from_chunk(
                chunk,
                archive=archive,
                rank=idx,
                score=item.fused_score,
                chunk_total=totals.get(chunk.session_id, archive.chunk_total if archive else 0),
            )
        )
    return hits


def _check_recall_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise RecallTimeoutError("recall timed out")


def _fetch_candidates(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    candidate_limit: int,
    embedder: Embedder | None = None,
    embedding_model: str = "hashing-embedder",
    deadline: float | None = None,
) -> tuple[list[tuple[str, float]], list[VectorHit]]:
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "sqlite":
        fts_hits = _fts_search(session, query=query, scope=scope, limit=candidate_limit)
        _check_recall_deadline(deadline)
        vector_hits = _vector_search(
            session,
            query=query,
            scope=scope,
            limit=candidate_limit,
            embedder=embedder,
            embedding_model=embedding_model,
        )
        return fts_hits, vector_hits
    return _parallel_fetch(
        bind,
        query=query,
        scope=scope,
        candidate_limit=candidate_limit,
        embedder=embedder,
        embedding_model=embedding_model,
        deadline=deadline,
    )


def _fetch_candidates_with_timeout(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    candidate_limit: int,
    timeout_ms: int,
    embedder: Embedder | None = None,
    embedding_model: str = "hashing-embedder",
) -> tuple[list[tuple[str, float]], list[VectorHit]]:
    deadline = time.monotonic() + (timeout_ms / 1000.0) if timeout_ms > 0 else None
    return _fetch_candidates(
        session,
        query=query,
        scope=scope,
        candidate_limit=candidate_limit,
        embedder=embedder,
        embedding_model=embedding_model,
        deadline=deadline,
    )


def _parallel_fetch(
    bind,
    *,
    query: str,
    scope: SearchScope,
    candidate_limit: int,
    embedder: Embedder | None = None,
    embedding_model: str = "hashing-embedder",
    deadline: float | None = None,
) -> tuple[list[tuple[str, float]], list[VectorHit]]:
    def run_fts() -> list[tuple[str, float]]:
        local = sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)()
        try:
            return _fts_search(local, query=query, scope=scope, limit=candidate_limit)
        finally:
            local.close()

    def run_vector() -> list[VectorHit]:
        local = sessionmaker(bind=bind, autoflush=False, autocommit=False, expire_on_commit=False)()
        try:
            return _vector_search(
                local,
                query=query,
                scope=scope,
                limit=candidate_limit,
                embedder=embedder,
                embedding_model=embedding_model,
            )
        finally:
            local.close()

    if bind is None:
        return [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        fts_future = pool.submit(run_fts)
        vec_future = pool.submit(run_vector)
        fts_hits = fts_future.result()
        _check_recall_deadline(deadline)
        vector_hits = vec_future.result()
        _check_recall_deadline(deadline)
        return fts_hits, vector_hits


def hybrid_search(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    config: AssistantChatMemoryConfig | MemoryRecallBudgetConfig,
    cursor: RecallCursor | None = None,
    embedder: Embedder | None = None,
    embedding_model: str | None = None,
    llm_api_key: str = "",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_timeout_seconds: float = 30.0,
    llm_enabled: bool = False,
) -> tuple[list[ArchiveHit], SearchPageMeta]:
    recall = config.recall if isinstance(config, AssistantChatMemoryConfig) else config
    if embedder is None and isinstance(config, AssistantChatMemoryConfig):
        embedder, resolved_model = build_archive_embedder(
            embedding=config.embedding,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_timeout_seconds=llm_timeout_seconds,
            llm_enabled=llm_enabled,
        )
        embedding_model = embedding_model or resolved_model
    resolved_embedding_model = embedding_model or "hashing-embedder"
    fingerprint = compute_query_fingerprint(query, scope)
    if cursor is not None and cursor.query_fingerprint != fingerprint:
        raise ValueError("cursor query_fingerprint mismatch")

    candidate_limit = max(recall.fragment_top_k * 5, recall.fragment_top_k, 10)
    fts_hits, vector_hits = _fetch_candidates_with_timeout(
        session,
        query=query,
        scope=scope,
        candidate_limit=candidate_limit,
        timeout_ms=recall.timeout_ms,
        embedder=embedder,
        embedding_model=resolved_embedding_model,
    )
    scored = _fuse_chunk_scores(
        fts_hits,
        vector_hits,
        fts_weight=recall.fts_weight,
        vector_weight=recall.vector_weight,
    )
    ranked = _build_hits(session, scored, scope)
    ranked = _dedupe_overlapping(ranked)
    ranked = _apply_session_diversity(ranked, recall.max_fragments_per_session)

    total_hits = len(ranked)
    offset = cursor.offset if cursor is not None else 0
    page_slice = ranked[offset : offset + recall.fragment_top_k]
    page_hits = tuple(
        hit.model_copy(update={"rank": offset + idx})
        for idx, hit in enumerate(page_slice, start=1)
    )

    returned = len(page_hits)
    next_offset = offset + returned
    has_more = next_offset < total_hits
    next_cursor = None
    if has_more:
        last = page_hits[-1] if page_hits else None
        next_cursor = RecallCursor(
            query_fingerprint=fingerprint,
            sort_key=_sort_key_for_hit(last) if last else f"{next_offset}",
            offset=next_offset,
        )

    page = SearchPageMeta(
        total_hits=total_hits,
        returned_count=returned,
        has_more=has_more,
        cursor=next_cursor,
    )
    return page_hits, page


def expand_neighbors(
    session: Session,
    *,
    anchor: ChunkAnchor,
    scope: SearchScope,
    neighbor_count: int = 2,
) -> NeighborWindow:
    if not _session_is_in_scope(session, anchor.session_id, scope):
        return NeighborWindow(anchor=anchor, prev_hits=(), next_hits=(), expand_count=0)

    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == anchor.session_id)
    )
    chunks = list(
        session.scalars(
            select(ArchiveChunkRow)
            .where(ArchiveChunkRow.session_id == anchor.session_id)
            .order_by(ArchiveChunkRow.chunk_index.asc())
        ).all()
    )
    chunk_total = len(chunks)
    by_index = {chunk.chunk_index: chunk for chunk in chunks}
    if anchor.chunk_index not in by_index:
        return NeighborWindow(anchor=anchor, prev_hits=(), next_hits=(), expand_count=0)

    prev_hits: list[ArchiveHit] = []
    next_hits: list[ArchiveHit] = []
    for delta in range(1, max(0, neighbor_count) + 1):
        prev_chunk = by_index.get(anchor.chunk_index - delta)
        if prev_chunk is not None:
            prev_hits.append(
                _archive_hit_from_chunk(
                    prev_chunk,
                    archive=archive,
                    rank=delta,
                    score=0.0,
                    chunk_total=chunk_total,
                )
            )
        next_chunk = by_index.get(anchor.chunk_index + delta)
        if next_chunk is not None:
            next_hits.append(
                _archive_hit_from_chunk(
                    next_chunk,
                    archive=archive,
                    rank=delta,
                    score=0.0,
                    chunk_total=chunk_total,
                )
            )
    prev_hits.sort(key=lambda hit: hit.chunk_index)
    next_hits.sort(key=lambda hit: hit.chunk_index)
    return NeighborWindow(
        anchor=anchor,
        prev_hits=tuple(prev_hits),
        next_hits=tuple(next_hits),
        expand_count=len(prev_hits) + len(next_hits),
    )


def read_message_range(
    session: Session,
    *,
    session_id: str,
    start_seq: int,
    end_seq: int,
    scope: SearchScope,
) -> list[ArchiveHit]:
    if start_seq > end_seq or not _session_is_in_scope(session, session_id, scope):
        return []
    archive = session.scalar(
        select(SessionArchiveRow).where(SessionArchiveRow.session_id == session_id)
    )
    if archive is None:
        return []
    messages = list(
        session.scalars(
            select(ArchiveMessageRow)
            .where(
                ArchiveMessageRow.session_id == session_id,
                ArchiveMessageRow.seq >= start_seq,
                ArchiveMessageRow.seq <= end_seq,
            )
            .order_by(ArchiveMessageRow.seq.asc())
        ).all()
    )
    if not messages:
        return []
    hits: list[ArchiveHit] = []
    for idx, message in enumerate(messages, start=1):
        text_body = f"{message.role}: {message.text_redacted or ''}".strip()
        occurred = _ensure_aware(message.created_at)
        hits.append(
            ArchiveHit(
                memory_id=f"{session_id}:seq:{message.seq}",
                session_id=session_id,
                source_type=MemorySourceType.ARCHIVE_CHUNK,
                scope=MemoryScope(archive.scope),
                text=text_body,
                source_roles=(message.role,),
                occurred_from=occurred,
                occurred_to=occurred,
                start_seq=message.seq,
                end_seq=message.seq,
                chunk_index=max(0, message.seq - 1),
                session_message_total=archive.message_total,
                session_chunk_total=archive.chunk_total,
                rank=idx,
                score=0.0,
                has_prev=message.seq > 1,
                has_next=message.seq < archive.message_total,
                anchor=ChunkAnchor(
                    session_id=session_id,
                    chunk_index=max(0, message.seq - 1),
                    start_seq=message.seq,
                    end_seq=message.seq,
                ),
            )
        )
    return hits


def _atom_source_type(kind: AtomKind) -> MemorySourceType:
    if kind == AtomKind.PREFERENCE:
        return MemorySourceType.PREFERENCE
    return MemorySourceType.FACT


def recall_fact_items(
    session: Session,
    *,
    query: str,
    scope: SearchScope,
    visibility_context: VisibilityContext,
    top_k: int = 5,
    memory_repo: SemanticMemoryRepository | None = None,
) -> list[FactRecallItem]:
    repo = memory_repo or SemanticMemoryRepository(session)
    namespace = team_id_to_namespace(scope.team_id)
    subject_ids = [scope.subject_id]
    try:
        disclosure = recall_memories(
            repo,
            None,
            namespace=namespace,
            subject_ids=subject_ids,
            context=visibility_context,
            query=query,
            log=False,
        )
    except Exception:
        logger.exception("semantic memory recall failed")
        return []

    items: list[FactRecallItem] = []
    for atom in disclosure.released_atoms:
        if atom.confidence < 0.5:
            continue
        score = 0.4
        if query:
            q = query.lower()
            if q in atom.content.lower():
                score = 1.0
            elif any(token in atom.content.lower() for token in q.split()):
                score = 0.7
        items.append(
            FactRecallItem(
                memory_id=atom.id,
                source_type=_atom_source_type(atom.kind),
                scope=scope.scope,
                subject_id=atom.subject_id,
                content=atom.content,
                confidence=atom.confidence,
                first_confirmed_at=atom.first_confirmed_at,
                last_confirmed_at=atom.last_seen_at,
                evidence_session_ids=atom.evidence_session_ids,
                score=score,
            )
        )

    from assistant_platform.memory.semantic.domain import CommitmentType

    commitments = repo.list_commitments(namespace, counterparty_ids=subject_ids or None)
    for commitment in commitments:
        if commitment.status != "active" or commitment.type != CommitmentType.PROMISED:
            continue
        score = 0.9 if query and query.lower() in commitment.statement.lower() else 0.6
        items.append(
            FactRecallItem(
                memory_id=commitment.id,
                source_type=MemorySourceType.COMMITMENT,
                scope=scope.scope,
                subject_id=commitment.counterparty_id,
                content=commitment.statement,
                confidence=0.8,
                first_confirmed_at=commitment.first_confirmed_at,
                last_confirmed_at=commitment.last_confirmed_at,
                evidence_session_ids=commitment.evidence_session_ids,
                score=score,
            )
        )

    items.sort(key=lambda item: (-item.score, item.memory_id))
    ranked: list[FactRecallItem] = []
    for idx, item in enumerate(items[: max(0, top_k)], start=1):
        ranked.append(item.model_copy(update={"rank": idx}))
    return ranked
