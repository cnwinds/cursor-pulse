from __future__ import annotations

import math
from typing import Protocol

import httpx

from personamem.retrieval import Embedder, HashingEmbedder, cosine_similarity


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


class OpenAIEmbedder(Embedder):
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


class CachedAtomEmbedder(Embedder):
    """优先读 atom 向量缓存，缺失时回退到内层 embedder 并写回。"""

    def __init__(self, inner: Embedder, repo, namespace: str):
        self._inner = inner
        self._repo = repo
        self._namespace = namespace

    def embed(self, text: str) -> list[float]:
        return self._inner.embed(text)

    def embed_atom(self, atom_id: str, content: str) -> list[float]:
        cached = self._repo.get_atom_embedding(atom_id)  # type: ignore[attr-defined]
        if cached:
            return cached
        vec = self._inner.embed(content)
        self._repo.save_atom_embedding(atom_id, vec)  # type: ignore[attr-defined]
        return vec


def rank_atoms_with_cache(
    atoms,
    query: str,
    embedder: CachedAtomEmbedder | Embedder | None,
    *,
    top_k: int | None = None,
):
    from personamem.retrieval import keyword_score, rank_atoms, tokenize

    if not atoms:
        return []
    if embedder is None:
        return rank_atoms(atoms, query, top_k=top_k)

    query_tokens = tokenize(query)
    query_vec = embedder.embed(query)

    scored = []
    for atom in atoms:
        kw = keyword_score(atom, query_tokens)
        if isinstance(embedder, CachedAtomEmbedder):
            atom_vec = embedder.embed_atom(atom.id, atom.content)
        else:
            atom_vec = embedder.embed(atom.content)
        sem = cosine_similarity(query_vec, atom_vec)
        score = 0.35 * kw + 0.65 * sem
        scored.append((score, atom))

    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [atom for score, atom in scored if score > 0] + [
        atom for score, atom in scored if score <= 0
    ]
    if top_k is not None:
        return ranked[:top_k]
    return ranked
