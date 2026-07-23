"""Recoverable historical archive/index backfill entry point."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from assistant_platform.config import load_assistant_config
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.memory.archive_indexer import archive_and_index_session
from assistant_platform.memory.archive_models import SessionArchiveRow
from assistant_platform.storage.db import init_assistant_db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillSummary:
    processed: int
    succeeded: int
    failed: int
    has_more: bool
    session_ids: tuple[str, ...] = ()


def find_backfill_candidates(
    session: Session,
    *,
    index_version: int,
    batch_size: int = 20,
    team_id: str | None = None,
    force_reindex: bool = False,
    exclude_ids: Sequence[str] | frozenset[str] | None = None,
) -> list[str]:
    """Closed sessions that still have ledger messages and need archive/index.

    Recoverable: callers can repeatedly request the next batch until empty.
    """
    stmt = (
        select(ChatSessionRow.id)
        .join(ChatMessageRow, ChatMessageRow.session_id == ChatSessionRow.id)
        .outerjoin(SessionArchiveRow, SessionArchiveRow.session_id == ChatSessionRow.id)
        .where(ChatSessionRow.status == "closed")
    )
    if team_id:
        stmt = stmt.where(ChatSessionRow.team_id == team_id)
    if exclude_ids:
        stmt = stmt.where(ChatSessionRow.id.not_in(list(exclude_ids)))

    if force_reindex:
        # Rebuild every closed session that still has ledger text (caller must
        # pass exclude_ids across batches to avoid an infinite loop).
        pass
    else:
        needs_work = or_(
            SessionArchiveRow.id.is_(None),
            SessionArchiveRow.index_version != index_version,
            SessionArchiveRow.status != "ready",
            SessionArchiveRow.archive_status != "ready",
            SessionArchiveRow.index_status != "ready",
        )
        stmt = stmt.where(needs_work)

    stmt = stmt.distinct().order_by(ChatSessionRow.closed_at.asc())
    stmt = stmt.limit(max(1, batch_size))
    return list(session.scalars(stmt).all())


def run_archive_backfill(
    session: Session,
    *,
    index_version: int = 2,
    batch_size: int = 20,
    team_id: str | None = None,
    force_reindex: bool = False,
    max_tokens_per_chunk: int = 512,
    overlap_tokens: int = 64,
    exclude_ids: Sequence[str] | frozenset[str] | None = None,
) -> BackfillSummary:
    candidates = find_backfill_candidates(
        session,
        index_version=index_version,
        batch_size=batch_size,
        team_id=team_id,
        force_reindex=force_reindex,
        exclude_ids=exclude_ids,
    )
    succeeded = 0
    failed = 0
    processed_ids: list[str] = []
    for session_id in candidates:
        session_row = session.get(ChatSessionRow, session_id)
        if session_row is None:
            continue
        processed_ids.append(session_id)
        try:
            archive_and_index_session(
                session,
                session_row,
                index_version=index_version,
                max_tokens_per_chunk=max_tokens_per_chunk,
                overlap_tokens=overlap_tokens,
            )
            session.commit()
            succeeded += 1
        except Exception:
            session.rollback()
            failed += 1
            logger.exception("backfill failed for session %s", session_id)

    skip = list(exclude_ids or []) + processed_ids
    more = find_backfill_candidates(
        session,
        index_version=index_version,
        batch_size=1,
        team_id=team_id,
        force_reindex=force_reindex,
        exclude_ids=skip if force_reindex else None,
    )
    return BackfillSummary(
        processed=len(processed_ids),
        succeeded=succeeded,
        failed=failed,
        has_more=bool(more),
        session_ids=tuple(processed_ids),
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Backfill permanent chat archives and indexes")
    parser.add_argument("--database-url", default="", help="Override ASSISTANT_DATABASE_URL")
    parser.add_argument("--team-id", default="", help="Limit to one team")
    parser.add_argument("--batch-size", type=int, default=0, help="Sessions per batch")
    parser.add_argument("--index-version", type=int, default=0, help="Target index version")
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Rebuild even when archive is already ready for this version",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=0,
        help="Stop after N batches (0 = until drained)",
    )
    args = parser.parse_args(argv)

    config = load_assistant_config()
    database_url = args.database_url.strip() or config.database_url
    index_version = args.index_version or config.chat_memory.archive.index_version
    batch_size = args.batch_size or config.chat_memory.backfill.batch_size
    team_id = args.team_id.strip() or None
    SessionLocal = init_assistant_db(database_url, team_id=config.team_id or "default")

    batches = 0
    total_succeeded = 0
    total_failed = 0
    seen: list[str] = []
    while True:
        db = SessionLocal()
        try:
            summary = run_archive_backfill(
                db,
                index_version=index_version,
                batch_size=batch_size,
                team_id=team_id,
                force_reindex=args.force_reindex,
                max_tokens_per_chunk=config.chat_memory.chunking.max_tokens_per_chunk,
                overlap_tokens=config.chat_memory.chunking.overlap_tokens,
                exclude_ids=seen if args.force_reindex else None,
            )
        finally:
            db.close()
        batches += 1
        total_succeeded += summary.succeeded
        total_failed += summary.failed
        seen.extend(summary.session_ids)
        logger.info(
            "backfill batch=%s processed=%s succeeded=%s failed=%s has_more=%s",
            batches,
            summary.processed,
            summary.succeeded,
            summary.failed,
            summary.has_more,
        )
        if not summary.has_more or summary.processed == 0:
            break
        if args.max_batches and batches >= args.max_batches:
            break

    logger.info(
        "backfill complete batches=%s succeeded=%s failed=%s",
        batches,
        total_succeeded,
        total_failed,
    )
    return 1 if total_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
