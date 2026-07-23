"""Build and keep the skill vector index in sync.

Factory + startup/periodic sync for :class:`SkillVectorIndex`. The embedder
matches the archive pipeline (OpenAI-compatible when configured, otherwise
``HashingEmbedder``); index lifecycle is independent of chat-memory/archive.
"""

from __future__ import annotations

import logging
import threading

from assistant_platform.config import AssistantConfig, resolve_effective_chat_memory
from assistant_platform.memory.embedder import build_archive_embedder
from assistant_platform.skills.registry import SkillRegistry
from assistant_platform.skills.vector_index import SkillVectorIndex

logger = logging.getLogger(__name__)


def build_skill_vector_index(
    session,
    config: AssistantConfig,
    *,
    registry: SkillRegistry | None = None,
) -> SkillVectorIndex:
    """Construct a :class:`SkillVectorIndex` bound to ``session``.

    Embedder resolution mirrors the archive pipeline: OpenAI-compatible when
    LLM + embedding are configured, otherwise a safe local ``HashingEmbedder``.
    """
    registry = registry or SkillRegistry()
    chat_memory = resolve_effective_chat_memory(config)
    embedder, embedding_model = build_archive_embedder(
        embedding=chat_memory.embedding,
        llm_api_key=config.llm.api_key,
        llm_base_url=config.llm.base_url,
        llm_timeout_seconds=config.llm.timeout_seconds,
        llm_enabled=config.llm.enabled,
    )
    return SkillVectorIndex(
        session,
        registry,
        embedder=embedder,
        config=config.skills_vector,
        embedding_model=embedding_model,
    )


def sync_skill_vector_index(session_factory, config: AssistantConfig) -> None:
    """Run one reconcile pass; failures are logged and swallowed."""
    session = session_factory()
    try:
        index = build_skill_vector_index(session, config)
        stats = index.sync()
        session.commit()
        logger.info(
            "skill vector sync upserted=%d deleted=%d unchanged=%d skipped=%d",
            stats.upserted,
            stats.deleted,
            stats.unchanged,
            stats.skipped,
        )
    except Exception:
        logger.exception("skill vector sync failed")
        session.rollback()
    finally:
        session.close()


def start_skill_vector_sync(
    session_factory,
    config: AssistantConfig,
    stop_event: threading.Event,
) -> threading.Thread | None:
    """Sync once at startup, then resync on a daemon timer.

    Returns the background thread (or ``None`` when skills/vector routing are
    disabled). The thread exits when ``stop_event`` is set.
    """
    if not config.skills_enabled or not config.skills_vector.enabled:
        logger.info("skill vector sync disabled; skipping index build")
        return None

    sync_skill_vector_index(session_factory, config)

    interval = max(5, int(config.skills_vector.resync_interval_seconds))

    def _loop() -> None:
        while not stop_event.wait(interval):
            sync_skill_vector_index(session_factory, config)

    thread = threading.Thread(
        target=_loop, name="skill-vector-sync", daemon=True
    )
    thread.start()
    logger.info("skill vector sync thread started interval=%ds", interval)
    return thread
