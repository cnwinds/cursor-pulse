"""Minimal encrypted Secret Store for Phase 1 capability execution.

Production must set ``ASSISTANT_SECRET_KEY``. For local/tests only, when that env
var is unset, encryption key material is derived via BLAKE2b-256 from
``ASSISTANT_SERVICE_TOKEN`` — never use that fallback in production.
"""

from __future__ import annotations

import base64
import hashlib
import os
import uuid
from datetime import datetime, timezone
from hashlib import blake2b

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from assistant_platform.storage.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SecretRow(Base):
    __tablename__ = "ap_secrets"

    ref_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def _derive_key_from_secret(raw: str) -> bytes:
    try:
        key = base64.urlsafe_b64decode(raw + "==")
        if len(key) in (16, 24, 32):
            return key
    except Exception:
        pass
    try:
        key = bytes.fromhex(raw)
        if len(key) in (16, 24, 32):
            return key
    except ValueError:
        pass
    return hashlib.sha256(raw.encode()).digest()


def resolve_encryption_key_bytes(*, secret_key: str = "", service_token: str = "") -> bytes:
    raw = (secret_key or "").strip()
    if raw:
        return _derive_key_from_secret(raw)
    token = (service_token or "").strip()
    if token:
        return blake2b(token.encode(), digest_size=32).digest()
    raise ValueError("ASSISTANT_SECRET_KEY or ASSISTANT_SERVICE_TOKEN is required")


def _encrypt(plaintext: str, *, secret_key: str = "", service_token: str = "") -> str:
    key = resolve_encryption_key_bytes(secret_key=secret_key, service_token=service_token)
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode(), None)
    return base64.urlsafe_b64encode(nonce + ct).decode()


def _decrypt(blob: str, *, secret_key: str = "", service_token: str = "") -> str:
    key = resolve_encryption_key_bytes(secret_key=secret_key, service_token=service_token)
    data = base64.urlsafe_b64decode(blob + "==")
    nonce, ct = data[:12], data[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def put_secret(
    session: Session,
    *,
    kind: str,
    plaintext: str,
    secret_key: str = "",
    service_token: str = "",
) -> str:
    ref_id = str(uuid.uuid4())
    ciphertext = _encrypt(plaintext, secret_key=secret_key, service_token=service_token)
    session.add(SecretRow(ref_id=ref_id, kind=kind, ciphertext=ciphertext))
    session.flush()
    return ref_id


def get_secret(
    session: Session,
    ref_id: str,
    *,
    secret_key: str = "",
    service_token: str = "",
) -> str | None:
    row = session.get(SecretRow, ref_id)
    if row is None:
        return None
    return _decrypt(row.ciphertext, secret_key=secret_key, service_token=service_token)


def delete_secret(session: Session, ref_id: str) -> bool:
    row = session.get(SecretRow, ref_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
