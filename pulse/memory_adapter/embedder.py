from __future__ import annotations

from personamem.embeddings import (
    CachedAtomEmbedder,
    HashingEmbedder,
    OpenAIEmbedder,
    OpenAIEmbeddingClient,
)
from personamem.retrieval import Embedder
from pulse.config import AppConfig


def build_embedder(config: AppConfig, repo=None, namespace: str | None = None) -> Embedder:
    llm = config.llm
    if llm.api_key and config.memory.embedding_enabled:
        try:
            client = OpenAIEmbeddingClient(
                api_key=llm.api_key,
                model=config.memory.embedding_model,
                base_url=llm.base_url,
                timeout_seconds=llm.timeout_seconds,
            )
            inner = OpenAIEmbedder(client)
            if repo is not None and namespace:
                return CachedAtomEmbedder(inner, repo, namespace)
            return inner
        except Exception:
            pass
    if repo is not None and namespace:
        return CachedAtomEmbedder(HashingEmbedder(), repo, namespace)
    return HashingEmbedder()
