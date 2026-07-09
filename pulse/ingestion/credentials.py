from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.crypto import decrypt_secret, encrypt_secret, mask_api_key
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount, AiAccountCredential


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

    def bind_cursor_api_key(
        self, *, account_id: str, api_key: str, member_id: str
    ) -> AiAccountCredential:
        account = self.session.get(AiAccount, account_id)
        if not account:
            raise ValueError("account not found")

        self.cursor_client.exchange_api_key(api_key)
        encrypted = encrypt_secret(api_key, self.encryption_key)
        now = datetime.now(timezone.utc)

        cred = self.session.scalar(
            select(AiAccountCredential).where(AiAccountCredential.account_id == account_id)
        )
        if cred:
            cred.encrypted_value = encrypted
            cred.key_hint = mask_api_key(api_key)
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
                bound_by_member_id=member_id,
                last_validated_at=now,
            )
            self.session.add(cred)
        self.session.commit()
        return cred

    def decrypt_api_key(self, cred: AiAccountCredential) -> str:
        return decrypt_secret(cred.encrypted_value, self.encryption_key)

    def revoke(self, account_id: str) -> None:
        cred = self.session.scalar(
            select(AiAccountCredential).where(AiAccountCredential.account_id == account_id)
        )
        if not cred:
            return
        cred.status = "revoked"
        cred.encrypted_value = ""
        cred.sync_enabled = False
        self.session.commit()

    def get_credential(self, account_id: str) -> AiAccountCredential | None:
        return self.session.scalar(
            select(AiAccountCredential).where(AiAccountCredential.account_id == account_id)
        )
