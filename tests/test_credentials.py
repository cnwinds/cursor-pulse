import base64
import os

import pytest

from pulse.ingestion.credentials import (
    AccountEmailMismatchError,
    _apply_key_account_identifier,
    rotate_credential_encryption,
)
from pulse.ingestion.crypto import decrypt_secret, encrypt_secret, mask_api_key
from pulse.storage.models import AiAccount


def test_apply_key_account_identifier_auto_fill():
    account = AiAccount(
        team_id="t",
        vendor_id="v",
        plan_id="p",
        account_identifier="",
    )
    _apply_key_account_identifier(account, "user@example.com")
    assert account.account_identifier == "user@example.com"


def test_apply_key_account_identifier_mismatch():
    account = AiAccount(
        team_id="t",
        vendor_id="v",
        plan_id="p",
        account_identifier="ledger@example.com",
    )
    with pytest.raises(AccountEmailMismatchError):
        _apply_key_account_identifier(account, "other@example.com")


def test_encrypt_decrypt_round_trip():
    raw_key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    plaintext = "sk-cursor-test-secret-key-12345"
    blob = encrypt_secret(plaintext, raw_key)
    assert decrypt_secret(blob, raw_key) == plaintext


def test_mask_api_key():
    assert mask_api_key("sk-abcdefghijklmnop") == "sk-ab...mnop"
    assert mask_api_key("short") == "***"
    assert mask_api_key("  sk-abc12345678  ") == "sk-ab...5678"


def test_encrypt_decrypt_with_passphrase_key():
    """Arbitrary passphrase keys (non-base64/non-hex) fall back to SHA-256."""
    raw_key = "abcdefg1234567890"
    plaintext = "crsr_test_api_key_abcdefghijklmnop"
    blob = encrypt_secret(plaintext, raw_key)
    assert decrypt_secret(blob, raw_key) == plaintext


def test_rotate_credential_encryption_reencrypts_blob():
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from pulse.storage.models import AiAccountCredential, Base

    old_key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    new_key = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
    plaintext = "crsr_rotate_test_key"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        AiAccountCredential(
            account_id="acc1",
            vendor_id="v1",
            credential_type="cursor_api_key",
            encrypted_value=encrypt_secret(plaintext, old_key),
            key_hint="crsr...",
            bound_by_member_id="m1",
        )
    )
    session.commit()

    stats = rotate_credential_encryption(session, old_key=old_key, new_key=new_key)
    assert stats == {"credentials": 1, "loan_aliases": 0, "proxy_keys": 0, "skipped": 0}

    cred = session.scalar(select(AiAccountCredential))
    assert cred is not None
    assert decrypt_secret(cred.encrypted_value, new_key) == plaintext
    with pytest.raises(Exception):
        decrypt_secret(cred.encrypted_value, old_key)
    session.close()
