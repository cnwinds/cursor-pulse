"""Embedding primitives for archive indexing and semantic recall.

Moved from ``personamem.retrieval`` / ``personamem.embeddings`` with no
external dependency. ``HashingEmbedder`` is the zero-dependency local/dev
fallback; ``OpenAIEmbedder`` is used once an LLM API key + embedding model are
configured (see ``assistant_platform.memory.embedder.build_archive_embedder``).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

import httpx

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """零依赖轻量向量：字符哈希袋，用于语义近似排序。

    Uses ``blake2b`` (not Python's built-in ``hash()``) to bucket tokens, so
    the resulting vector is stable across processes and runs — Python's
    ``hash()`` for strings is randomized per-process (``PYTHONHASHSEED``)
    unless disabled, which would make stored embeddings incomparable across
    worker restarts.
    """

    def __init__(self, dimensions: int = 128):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        tokens = tokenize(text)
        if not tokens:
            return vec
        for token in tokens:
            for part in (token, token[:2], token[-2:]):
                idx = self._stable_index(part)
                vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def _stable_index(self, part: str) -> int:
        digest = hashlib.blake2b(part.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimensions


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


class EmbeddingClient(Protocol):
    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbeddingClient:
    """OpenAI 兼容 /embeddings 端点。"""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "text-embedding-3-small",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        items = sorted(data["data"], key=lambda item: item["index"])
        vectors: list[list[float]] = []
        for item in items:
            vec = item["embedding"]
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            vectors.append([v / norm for v in vec])
        return vectors


class OpenAIEmbedder:
    def __init__(self, client: EmbeddingClient):
        self._client = client
        self._cache: dict[str, list[float]] = {}

    def embed(self, text: str) -> list[float]:
        key = text.strip()
        if not key:
            return []
        if key in self._cache:
            return self._cache[key]
        vec = self._client.embed_texts([key])[0]
        self._cache[key] = vec
        return vec
