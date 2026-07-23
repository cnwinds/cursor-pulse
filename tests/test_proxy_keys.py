from __future__ import annotations

from pulse.proxy.keys import generate_proxy_key, hash_proxy_key


def test_generate_proxy_key_format():
    plaintext, key_hash, hint = generate_proxy_key()
    assert plaintext.startswith("pk_")
    assert len(plaintext) == 46
    assert key_hash == hash_proxy_key(plaintext)
    assert len(key_hash) == 64
    assert hint.startswith("pk_")
    assert len(hint) == 11


def test_generate_proxy_key_unique():
    a, b = generate_proxy_key(), generate_proxy_key()
    assert a[0] != b[0]
    assert a[1] != b[1]


def test_hash_is_sha256():
    import hashlib

    assert hash_proxy_key("pk_x") == hashlib.sha256(b"pk_x").hexdigest()
