from __future__ import annotations

import hashlib
import secrets


def generate_proxy_key() -> tuple[str, str, str]:
    """返回 (明文 key, sha256 哈希, 展示 hint)。明文会加密落库，创建响应仍返回一次。"""
    plaintext = "pk_" + secrets.token_urlsafe(32)
    return plaintext, hash_proxy_key(plaintext), plaintext[:11]


def generate_alias_key() -> tuple[str, str, str]:
    """借用别名 Key：pka_…，绑定 loan 上的 Cursor Key，不进共享池。"""
    plaintext = "pka_" + secrets.token_urlsafe(32)
    return plaintext, hash_proxy_key(plaintext), plaintext[:12]


def hash_proxy_key(plaintext: str) -> str:
    """鉴权查询时将入参明文转为 DB 查询用的哈希值。"""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
