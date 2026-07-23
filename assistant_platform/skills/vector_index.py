"""Skill vector index: sync skill files → ``ap_skill_embeddings`` and route cards.

Separate from archive/chat-memory embeddings. Each skill markdown file gets one
row keyed by ``skill_id``; rows are synced by file ``content_hash`` (upsert on
change, delete when the file disappears). ``route_cards`` embeds the current turn
text, filters by actor audience, keeps cosine hits above ``score_threshold`` and
returns the top-k :class:`SkillCard` objects for injection.

The Embedder boundary matches ``assistant_platform.memory.embedding`` so tests
use ``HashingEmbedder`` and production can swap in the OpenAI embedder (same
pattern as archive). See
docs/superpowers/specs/2026-07-21-file-as-skill-vector-routing-design.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from assistant_platform.config import SkillsVectorConfig
from assistant_platform.memory.embedding import Embedder, HashingEmbedder, cosine_similarity
from assistant_platform.skills.models import SkillActorContext, SkillCard
from assistant_platform.skills.registry import SkillRegistry
from assistant_platform.storage.models import SkillEmbeddingRow

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SkillSyncStats:
    upserted: int = 0
    deleted: int = 0
    skipped: int = 0
    unchanged: int = 0


class SkillVectorIndex:
    """SQLite JSON embedding store + cosine routing for skill cards."""

    def __init__(
        self,
        session: Session,
        registry: SkillRegistry,
        *,
        embedder: Embedder | None = None,
        config: SkillsVectorConfig | None = None,
        embedding_model: str = "hashing-embedder",
    ) -> None:
        self._session = session
        self._registry = registry
        self._embedder = embedder or HashingEmbedder()
        self._config = config or SkillsVectorConfig()
        self._embedding_model = embedding_model

    def sync(self, registry: SkillRegistry | None = None) -> SkillSyncStats:
        """Reconcile ``ap_skill_embeddings`` with the current skill files."""
        registry = registry or self._registry
        sources = registry.index_sources()
        existing = {
            row.skill_id: row
            for row in self._session.scalars(select(SkillEmbeddingRow)).all()
        }
        seen: set[str] = set()
        upserted = skipped = unchanged = 0

        for source in sources:
            seen.add(source.skill_id)
            row = existing.get(source.skill_id)
            if (
                row is not None
                and row.content_hash == source.content_hash
                and row.embedding_model == self._embedding_model
            ):
                unchanged += 1
                continue
            try:
                vector = self._embedder.embed(source.embed_text)
            except Exception:  # embed failure must not abort the whole sync
                logger.exception("skill embed failed; skipping %s", source.skill_id)
                skipped += 1
                continue
            if not vector:
                logger.warning("skill embed empty; skipping %s", source.skill_id)
                skipped += 1
                continue
            if row is None:
                row = SkillEmbeddingRow(skill_id=source.skill_id)
                self._session.add(row)
            row.rel_path = source.rel_path
            row.content_hash = source.content_hash
            row.audience_json = sorted(source.audience)
            row.embedding_json = list(vector)
            row.embedding_model = self._embedding_model
            row.updated_at = _utcnow()
            upserted += 1

        deleted = 0
        for skill_id, row in existing.items():
            if skill_id not in seen:
                self._session.delete(row)
                deleted += 1

        self._session.flush()
        return SkillSyncStats(
            upserted=upserted,
            deleted=deleted,
            skipped=skipped,
            unchanged=unchanged,
        )

    def route_cards(self, query: str, actor: SkillActorContext) -> list[SkillCard]:
        """Return top-k visible skill cards semantically matching ``query``."""
        if not self._config.enabled:
            return []
        query_vec = self._embedder.embed(query or "")
        if not query_vec:
            return []
        visible = {card.skill_id: card for card in self._registry.list_cards(actor)}
        if not visible:
            return []

        rows = self._session.scalars(select(SkillEmbeddingRow)).all()
        scored: list[tuple[float, str]] = []
        for row in rows:
            if row.skill_id not in visible:
                continue
            if not row.embedding_json:
                continue
            score = cosine_similarity(query_vec, list(row.embedding_json))
            if score >= self._config.score_threshold:
                scored.append((score, row.skill_id))

        scored.sort(key=lambda item: (-item[0], item[1]))
        top = scored[: max(0, self._config.top_k)]
        return [visible[skill_id] for _score, skill_id in top]
