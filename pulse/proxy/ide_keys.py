from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy.keys import generate_ide_key, hash_proxy_key
from pulse.storage.models import KeyLoan, ProxyKey


def find_ide_key_parent(session: Session, plaintext: str) -> ProxyKey | KeyLoan | None:
    h = hash_proxy_key(plaintext)
    key = session.scalar(select(ProxyKey).where(ProxyKey.ide_key_hash == h))
    if key is not None:
        return key
    return session.scalar(select(KeyLoan).where(KeyLoan.ide_key_hash == h))


def get_or_issue_ide_key(session: Session, parent: ProxyKey | KeyLoan, encryption_key: str) -> str:
    _validate_parent(parent)
    enc = (encryption_key or "").strip()
    if not enc:
        return _issue_new(session, parent, encryption_key)
    existing = _decrypt_ide_key(parent, enc)
    if existing:
        return existing
    return _issue_new(session, parent, encryption_key)


def rotate_ide_key(session: Session, parent: ProxyKey | KeyLoan, encryption_key: str) -> str:
    _validate_parent(parent)
    return _issue_new(session, parent, encryption_key)


def _validate_parent(parent: ProxyKey | KeyLoan) -> None:
    if isinstance(parent, ProxyKey):
        if parent.mode != "quota":
            raise ValueError("IDE keys only supported for quota proxy keys")
        return
    if not isinstance(parent, KeyLoan):
        raise TypeError(f"unsupported parent type: {type(parent)!r}")
    from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS

    if (getattr(parent, "delivery_mode", None) or "") != DELIVERY_PROXY_ALIAS:
        raise ValueError("IDE keys only supported for proxy_alias loans")


def _decrypt_ide_key(parent: ProxyKey | KeyLoan, encryption_key: str) -> str | None:
    enc_value = parent.ide_encrypted_key
    if not enc_value:
        return None
    from pulse.ingestion.crypto import decrypt_secret

    try:
        return decrypt_secret(enc_value, encryption_key.strip())
    except Exception:
        return None


def _issue_new(session: Session, parent: ProxyKey | KeyLoan, encryption_key: str) -> str:
    from pulse.ingestion.crypto import encrypt_secret

    plaintext, key_hash, hint = generate_ide_key()
    encrypted = None
    if (encryption_key or "").strip():
        encrypted = encrypt_secret(plaintext, encryption_key.strip())
    parent.ide_key_hash = key_hash
    parent.ide_key_hint = hint
    parent.ide_encrypted_key = encrypted
    session.flush()
    return plaintext
