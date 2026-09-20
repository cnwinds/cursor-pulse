from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, KeyLoan
from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS, LENDER_MODE_AUTO, LENDER_MODE_MANUAL
from pulse.tool_center.key_loan_state import KeyLoanStateMixin
from pulse.tool_center.quota_reads import latest_snapshots_for_accounts


def resolve_borrowed_cents(
    lender_mode: str | None, snapshot_cents: int, proxy_cents: int
) -> tuple[int, str]:
    """借用消耗口径 → ``(cents, basis)``。

    自动分配借用的流量会在候选账号间游走，单一账号的快照差值不再代表本笔借用
    的消耗，此时以代理账本按 ``loan_id`` 汇总为准；manual 或无账本记录时回退
    快照近似。看板与 IM 列表共用这一份，避免两处口径漂移。
    """
    if (lender_mode or "") == LENDER_MODE_AUTO and proxy_cents > 0:
        return int(proxy_cents), "proxy"
    return int(snapshot_cents), "quota_approx"


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
        lender_mode: str = LENDER_MODE_MANUAL,
        source_bound_at: datetime | None = None,
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
            lender_mode=lender_mode,
            source_bound_at=source_bound_at or datetime.now(timezone.utc),
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

    def approximate_borrowed_cents(
        self, loan: KeyLoan, proxy_cents: int | None = None
    ) -> int:
        """借用消耗（cents）。

        自动分配借用的流量会在候选账号间游走，单一账号的快照差值不再代表本笔
        借用的消耗；此时以代理账本按 ``loan_id`` 汇总为准（manual 保持快照近似）。

        ``proxy_cents`` 由调用方批量传入可避免逐笔查询，见
        :func:`pulse.proxy.usage_queries.loan_proxy_totals_by_loan`。
        """
        if proxy_cents is None:
            proxy_cents = 0
            if (getattr(loan, "lender_mode", None) or "") == LENDER_MODE_AUTO:
                from pulse.proxy.usage_queries import loan_proxy_totals

                _, proxy_cents = loan_proxy_totals(self.session, loan.id)
        snapshot = self.latest_snapshot(loan.source_account_id)
        snapshot_cents = (
            max(snapshot.used_cents - loan.baseline_used_cents, 0) if snapshot else 0
        )
        cents, _ = resolve_borrowed_cents(
            getattr(loan, "lender_mode", None), snapshot_cents, proxy_cents
        )
        return cents

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
