from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, KeyLoan
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS
from pulse.tool_center.key_loan_state import KeyLoanStateMixin
from pulse.tool_center.quota_reads import latest_snapshots_for_accounts


class KeyLoanService(KeyLoanStateMixin):
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
        self.credential_service = CredentialService(
            session, encryption_key, cursor_client=self.cursor_client
        )

    def latest_snapshot(self, account_id: str) -> AccountQuotaSnapshot | None:
        return latest_snapshots_for_accounts(self.session, [account_id]).get(account_id)

    def create_loan_record(
        self,
        *,
        source_account_id: str,
        credential_id: str,
        borrower_member_id: str,
        baseline_used_cents: int,
        auto_revoke_on_reset: bool = True,
        expires_on: date | None = None,
        note: str | None = None,
        delivery_mode: str = DELIVERY_PROXY_ALIAS,
        alias_key_hash: str | None = None,
        alias_key_hint: str | None = None,
        alias_encrypted_key: str | None = None,
    ) -> KeyLoan:
        loan = KeyLoan(
            source_account_id=source_account_id,
            credential_id=credential_id,
            borrower_member_id=borrower_member_id,
            baseline_used_cents=baseline_used_cents,
            auto_revoke_on_reset=auto_revoke_on_reset,
            expires_on=expires_on,
            note=note,
            status="active",
            delivery_mode=delivery_mode,
            alias_key_hash=alias_key_hash,
            alias_key_hint=alias_key_hint,
            alias_encrypted_key=alias_encrypted_key,
        )
        self.session.add(loan)
        self.session.flush()
        return loan

    def list_loans(self, *, status: str | None = None) -> list[KeyLoan]:
        query = select(KeyLoan).order_by(KeyLoan.created_at.desc())
        if status:
            query = query.where(KeyLoan.status == status)
        return list(self.session.scalars(query).all())

    def list_active_loans(self) -> list[KeyLoan]:
        return self.list_loans(status="active")

    def get_loan(self, loan_id: str) -> KeyLoan | None:
        return self.session.get(KeyLoan, loan_id)

    def approximate_borrowed_cents(self, loan: KeyLoan) -> int:
        snapshot = self.latest_snapshot(loan.source_account_id)
        if not snapshot:
            return 0
        return max(snapshot.used_cents - loan.baseline_used_cents, 0)

    def active_loan_for_borrower(self, borrower_member_id: str) -> KeyLoan | None:
        loans = self.list_active_loans_for_borrower(borrower_member_id)
        return loans[0] if loans else None

    def list_active_loans_for_borrower(self, borrower_member_id: str) -> list[KeyLoan]:
        return list(
            self.session.scalars(
                select(KeyLoan)
                .where(
                    KeyLoan.borrower_member_id == borrower_member_id,
                    KeyLoan.status == "active",
                )
                .order_by(KeyLoan.created_at.desc())
            ).all()
        )

    def list_active_loans_for_team(self, team_id: str) -> list[KeyLoan]:
        return list(
            self.session.scalars(
                select(KeyLoan)
                .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
                .where(AiAccount.team_id == team_id, KeyLoan.status == "active")
                .order_by(KeyLoan.created_at.desc())
            ).all()
        )
