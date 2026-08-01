from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from pulse.storage.models import AiAccount, AiAccountCredential, KeyLoan
from pulse.tool_center.key_loan_lender import _loan_created_date, account_loan_deadline

logger = logging.getLogger(__name__)


class KeyLoanStateMixin:
    """LoanState: revoke · expire · notify flush. Mixed into KeyLoanService."""

    def revoke_loan(
        self,
        loan_id: str,
        *,
        revoke_remote: bool = True,
    ) -> tuple[KeyLoan, int]:
        loan = self.get_loan(loan_id)
        if not loan:
            raise ValueError("loan not found")
        if loan.status != "active":
            borrowed = self.approximate_borrowed_cents(loan)
            return loan, borrowed

        borrowed = self.approximate_borrowed_cents(loan)
        cred = self.session.get(AiAccountCredential, loan.credential_id)
        if revoke_remote and cred and cred.remote_key_id and cred.status == "active":
            primary = self.credential_service.get_primary_credential(loan.source_account_id)
            if primary:
                api_key = self.credential_service.decrypt_api_key(primary)
                token = self.cursor_client.get_access_token(api_key)
                self.cursor_client.revoke_user_api_key(
                    token, cred.remote_key_id, api_key=api_key
                )
        if cred:
            cred.status = "revoked"
            cred.sync_enabled = False
            cred.encrypted_value = ""

        loan.status = "revoked"
        loan.revoked_at = datetime.now(timezone.utc)
        # 清空别名，防止误恢复 / 泄漏哈希侧信道
        loan.alias_key_hash = None
        loan.alias_key_hint = None
        loan.alias_encrypted_key = None
        self.session.flush()
        return loan, borrowed

    def expire_loans_on_reset(
        self,
        today: date | None = None,
        *,
        account_id: str | None = None,
        notify_config=None,
    ) -> int:
        """Expire due loans. When ``notify_config`` is set, callers must
        ``commit`` first, then call :meth:`flush_expire_notifications`.
        """
        today = today or date.today()
        expired = 0
        pending: list[tuple[KeyLoan, int]] = []
        loans = self.list_active_loans()
        for loan in loans:
            if account_id and loan.source_account_id != account_id:
                continue
            if not self.loan_should_auto_expire(loan, today=today):
                continue
            borrowed_cents = 0
            try:
                loan, borrowed_cents = self.revoke_loan(loan.id, revoke_remote=True)
                revoked_remote = True
            except Exception:
                revoked_remote = False
                logger.warning(
                    "loan %s 远端撤销失败，转为仅本地过期", loan.id, exc_info=True
                )
            if not revoked_remote:
                try:
                    loan, borrowed_cents = self.revoke_loan(loan.id, revoke_remote=False)
                except Exception:
                    logger.error("loan %s 本地过期失败", loan.id, exc_info=True)
                    continue
            loan.status = "expired"
            expired += 1
            if notify_config is not None:
                pending.append((loan, borrowed_cents))
        self._pending_expire_notifies = pending if notify_config is not None else []
        self._expire_notify_config = notify_config
        return expired

    def flush_expire_notifications(self) -> None:
        """Send reclaim IM after the expire transaction has been committed."""
        pending = getattr(self, "_pending_expire_notifies", None) or []
        notify_config = getattr(self, "_expire_notify_config", None)
        self._pending_expire_notifies = []
        self._expire_notify_config = None
        if not pending or notify_config is None:
            return
        from pulse.tool_center.key_loan_notify import notify_loan_reclaimed

        for loan, borrowed_cents in pending:
            try:
                notify_loan_reclaimed(
                    self.session,
                    notify_config,
                    loan=loan,
                    borrowed_cents=borrowed_cents,
                    reason="expired",
                )
            except Exception:
                logger.exception(
                    "key loan expire notify failed loan=%s", loan.id
                )

    def loan_should_auto_expire(
        self, loan: KeyLoan, *, today: date | None = None
    ) -> bool:
        """Whether an active loan should be reclaimed for billing-cycle reset.

        Triggers:
        1. Frozen ``expires_on <= today`` (primary path for new loans)
        2. Legacy null ``expires_on`` only:
           - snapshot cycle rolled past loan creation, or
           - live account deadline <= today
        """
        if not loan.auto_revoke_on_reset or loan.status != "active":
            return False
        today = today or date.today()
        if loan.expires_on is not None:
            return loan.expires_on <= today
        # Legacy rows without a frozen deadline (pre-migration / stuck after sync).
        snapshot = self.latest_snapshot(loan.source_account_id)
        if snapshot and _loan_created_date(loan) < snapshot.cycle_start:
            return True
        account = self.session.get(AiAccount, loan.source_account_id)
        if not account:
            return False
        deadline = account_loan_deadline(account)
        return bool(deadline and deadline <= today)

