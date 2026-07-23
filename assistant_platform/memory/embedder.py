"""Embedder factory for chat archive indexing and recall."""

from __future__ import annotations

import logging

from assistant_platform.config import MemoryEmbeddingConfig
from assistant_platform.memory.embedding import (
    Embedder,
    HashingEmbedder,
    OpenAIEmbedder,
    OpenAIEmbeddingClient,
)

logger = logging.getLogger(__name__)

_HASHING_MODEL = "hashing-embedder-v2"


def build_archive_embedder(
    *,
    embedding: MemoryEmbeddingConfig,
    llm_api_key: str = "",
    llm_base_url: str = "https://api.openai.com/v1",
    llm_timeout_seconds: float = 30.0,
    llm_enabled: bool = False,
) -> tuple[Embedder, str]:
    """Return embedder and model label stored on chunk rows.

    Uses OpenAI-compatible embeddings when ``embedding.enabled``, LLM is
    enabled, a model is configured, and ``llm_api_key`` is present; otherwise
    falls back to ``HashingEmbedder`` (transitional default for local/dev).
    """
    model_name = (embedding.model or "").strip() or _HASHING_MODEL
    if (
        embedding.enabled
        and llm_enabled
        and llm_api_key.strip()
        and model_name != _HASHING_MODEL
    ):
        try:
            client = OpenAIEmbeddingClient(
                api_key=llm_api_key.strip(),
                model=model_name,
                base_url=llm_base_url.strip() or "https://api.openai.com/v1",
                timeout_seconds=llm_timeout_seconds,
            )
            return OpenAIEmbedder(client), model_name
        except Exception:
            logger.exception("failed to build OpenAI embedder; using hashing fallback")
    return HashingEmbedder(), _HASHING_MODEL
