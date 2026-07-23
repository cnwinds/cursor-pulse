from __future__ import annotations

from assistant_platform.memory.embedding import HashingEmbedder


def test_hashing_embedder_deterministic_across_instances():
    """Two independent HashingEmbedder instances must embed the same text to
    the same vector — the index buckets are derived from a stable hash
    (blake2b), not Python's per-process-randomized ``hash()``, so stored
    embeddings stay comparable across process/worker restarts.
    """
    text = "我的额度 quota 用量 usage 还剩多少"
    vec_a = HashingEmbedder(dimensions=128).embed(text)
    vec_b = HashingEmbedder(dimensions=128).embed(text)
    assert vec_a == vec_b
    assert any(v != 0.0 for v in vec_a)


def test_hashing_embedder_deterministic_repeated_calls():
    embedder = HashingEmbedder(dimensions=64)
    text = "blue project deadline next week"
    assert embedder.embed(text) == embedder.embed(text)


def test_hashing_embedder_empty_text_returns_zero_vector():
    embedder = HashingEmbedder(dimensions=32)
    vec = embedder.embed("")
    assert vec == [0.0] * 32
