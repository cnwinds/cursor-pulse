from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _derive_key(raw: str) -> bytes:
    if not raw:
        raise ValueError("PULSE_CREDENTIAL_ENCRYPTION_KEY is required")
    try:
        key = base64.urlsafe_b64decode(raw + "==")
    except Exception:
        key = bytes.fromhex(raw)
    if len(key) not in (16, 24, 32):
        key = hashlib.sha256(raw.encode()).digest()
    return key


def encrypt_secret(plaintext: str, raw_key: str) -> str:
    key = _derive_key(raw_key)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def decrypt_secret(blob: str, raw_key: str) -> str:
    key = _derive_key(raw_key)
    data = base64.urlsafe_b64decode(blob + "==")
    nonce, ct = data[:12], data[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def mask_api_key(api_key: str) -> str:
    api_key = api_key.strip()
    if len(api_key) <= 8:
        return "***"
    return f"{api_key[:5]}...{api_key[-4:]}"
