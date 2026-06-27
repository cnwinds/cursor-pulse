from __future__ import annotations

import math
import re
from typing import Protocol

from personamem.domain import MemoryAtom

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text) if len(t) > 1]


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """零依赖轻量向量：字符哈希袋，用于语义近似排序。"""

    def __init__(self, dimensions: int = 128):
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dimensions
        tokens = tokenize(text)
        if not tokens:
            return vec
        for token in tokens:
            for part in (token, token[:2], token[-2:]):
                idx = hash(part) % self.dimensions
                vec[idx] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def keyword_score(atom: MemoryAtom, query_tokens: list[str]) -> float:
    content = atom.content.lower()
    if not query_tokens:
        return 0.0
    hits = sum(1 for token in query_tokens if token in content)
    return hits / len(query_tokens)


def rank_atoms(
    atoms: list[MemoryAtom],
    query: str,
    *,
    embedder: Embedder | None = None,
    top_k: int | None = None,
) -> list[MemoryAtom]:
    if not atoms:
        return []
    query_tokens = tokenize(query)
    query_vec = embedder.embed(query) if embedder else None

    scored: list[tuple[float, MemoryAtom]] = []
    for atom in atoms:
        kw = keyword_score(atom, query_tokens)
        if embedder and query_vec:
            atom_vec = embedder.embed(atom.content)
            sem = cosine_similarity(query_vec, atom_vec)
            score = 0.4 * kw + 0.6 * sem
        else:
            score = kw
        scored.append((score, atom))

    scored.sort(key=lambda item: item[0], reverse=True)
    ranked = [atom for score, atom in scored if score > 0] + [
        atom for score, atom in scored if score <= 0
    ]
    if top_k is not None:
        return ranked[:top_k]
    return ranked
