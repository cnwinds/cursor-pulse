from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.crypto import decrypt_secret, encrypt_secret, mask_api_key
from pulse.proxy.keys import hash_proxy_key
from pulse.ingestion.sync_schedule import init_schedule_on_bind
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount, AiAccountCredential


class AccountEmailMismatchError(ValueError):
    def __init__(self, *, ledger_email: str, key_email: str):
        self.ledger_email = ledger_email
        self.key_email = key_email
        super().__init__(
            f"API Key 对应账号 {key_email} 与台账账号 {ledger_email} 不一致"
        )


def _ledger_identifier(account: AiAccount) -> str | None:
    text = (account.account_identifier or "").strip()
    return text.lower() if text else None


def _apply_key_account_identifier(
    account: AiAccount, key_email: str | None
) -> None:
    ledger_id = _ledger_identifier(account)
    if not ledger_id:
        if not key_email:
            raise ValueError("无法从 API Key 解析账号标识，请先手工填写或更换有效 Key")
        account.account_identifier = key_email
        return
    if not key_email:
        raise ValueError("无法从 API Key 解析账号标识，无法校验是否与台账一致")
    if key_email != ledger_id:
        raise AccountEmailMismatchError(
            ledger_email=account.account_identifier,
            key_email=key_email,
        )


class CredentialService:
    def __init__(
        self,
        session: Session,
        encryption_key: str,
        *,
        cursor_client: CursorApiClient | None = None,
    ):
        self.session = session
        self.encryption_key = encryption_key
        self.cursor_client = cursor_client or CursorApiClient()

    def get_primary_credential(self, account_id: str) -> AiAccountCredential | None:
        cred = self.session.scalar(
            select(AiAccountCredential).where(
                AiAccountCredential.account_id == account_id,
                AiAccountCredential.key_role == "primary",
                AiAccountCredential.status == "active",
            )
        )
        if cred:
            return cred
        return self.session.scalar(
            select(AiAccountCredential).where(
                AiAccountCredential.account_id == account_id,
                AiAccountCredential.status == "active",
            )
        )

    def bind_cursor_api_key(
        self,
        *,
        account_id: str,
        api_key: str,
        member_id: str,
    ) -> AiAccountCredential:
        account = self.session.get(AiAccount, account_id)
        if not account:
            raise ValueError("account not found")

        exchange = self.cursor_client.exchange_user_api_key_response(api_key)
        key_email = self.cursor_client.resolve_api_key_account_email(
            api_key, exchange=exchange
        )
        _apply_key_account_identifier(account, key_email)
        encrypted = encrypt_secret(api_key, self.encryption_key)
        now = datetime.now(timezone.utc)

        cred = self.get_primary_credential(account_id)
        key_hash = hash_proxy_key(api_key)
        if cred:
            cred.key_role = "primary"
            cred.encrypted_value = encrypted
            cred.key_hint = mask_api_key(api_key)
            cred.key_hash = key_hash
            cred.status = "active"
            cred.bound_by_member_id = member_id
            cred.bound_at = now
            cred.last_validated_at = now
            cred.sync_enabled = True
        else:
            cred = AiAccountCredential(
                account_id=account_id,
                vendor_id=account.vendor_id,
                credential_type="cursor_api_key",
                encrypted_value=encrypted,
                key_hint=mask_api_key(api_key),
                key_hash=key_hash,
                key_role="primary",
                bound_by_member_id=member_id,
                last_validated_at=now,
            )
            self.session.add(cred)
        init_schedule_on_bind(cred)
        self.session.commit()
        return cred

    def create_loan_credential(
        self,
        *,
        account_id: str,
        api_key: str,
        display_name: str,
        remote_key_id: int | None,
        assignee_member_id: str,
        bound_by_member_id: str,
    ) -> AiAccountCredential:
        account = self.session.get(AiAccount, account_id)
        if not account:
            raise ValueError("account not found")

        encrypted = encrypt_secret(api_key, self.encryption_key)
        now = datetime.now(timezone.utc)
        cred = AiAccountCredential(
            account_id=account_id,
            vendor_id=account.vendor_id,
            credential_type="cursor_api_key",
            encrypted_value=encrypted,
            key_hint=mask_api_key(api_key),
            key_hash=hash_proxy_key(api_key),
            key_role="loan",
            display_name=display_name,
            remote_key_id=remote_key_id,
            assignee_member_id=assignee_member_id,
            bound_by_member_id=bound_by_member_id,
            last_validated_at=now,
            sync_enabled=False,
        )
        self.session.add(cred)
        self.session.flush()
        return cred

    def decrypt_api_key(self, cred: AiAccountCredential) -> str:
        return decrypt_secret(cred.encrypted_value, self.encryption_key)

    def revoke(self, account_id: str) -> None:
        cred = self.get_primary_credential(account_id)
        if not cred:
            return
        cred.status = "revoked"
        cred.encrypted_value = ""
        cred.sync_enabled = False
        self.session.commit()

    def get_credential(self, account_id: str) -> AiAccountCredential | None:
        return self.get_primary_credential(account_id)


def backfill_credential_key_hashes(session: Session, encryption_key: str) -> int:
    """Decrypt active credentials missing key_hash and write sha256. Returns count updated."""
    enc = (encryption_key or "").strip()
    if not enc:
        return 0
    creds = session.scalars(
        select(AiAccountCredential).where(
            AiAccountCredential.key_hash.is_(None),
            AiAccountCredential.encrypted_value != "",
            AiAccountCredential.status == "active",
        )
    ).all()
    updated = 0
    for cred in creds:
        try:
            plain = decrypt_secret(cred.encrypted_value, enc)
        except Exception:
            continue
        cred.key_hash = hash_proxy_key(plain)
        updated += 1
    if updated:
        session.commit()
    return updated
