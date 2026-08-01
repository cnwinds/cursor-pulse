from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, AiAccountCredential, KeyLoan, Member
from pulse.tool_center.key_loan_delivery import (
    DELIVERY_CURSOR_DIRECT,
    DELIVERY_PROXY_ALIAS,
    KeyLoanError,
)
from pulse.tool_center.key_loan_lender import loan_display_expires_on
from pulse.proxy.usage_queries import loan_proxy_totals
from pulse.util.datetime_fmt import tool_datetime

def loan_payload(loan: KeyLoan, session: Session) -> dict:
    borrower_name = None
    if loan.borrower_member_id:
        member = session.get(Member, loan.borrower_member_id)
        borrower_name = member.display_name if member else None
    account = session.get(AiAccount, loan.source_account_id)
    primary_member_name = None
    if account and account.primary_member_id:
        primary = session.get(Member, account.primary_member_id)
        primary_member_name = primary.display_name if primary else None
    borrowed_cents = max(
        (session.scalar(
            select(AccountQuotaSnapshot.used_cents)
            .where(AccountQuotaSnapshot.account_id == loan.source_account_id)
            .order_by(AccountQuotaSnapshot.captured_at.desc())
            .limit(1)
        ) or 0)
        - loan.baseline_used_cents,
        0,
    )
    deadline = loan_display_expires_on(loan, account)
    _, proxy_cost_cents = loan_proxy_totals(session, loan.id)
    delivery_mode = getattr(loan, "delivery_mode", None) or DELIVERY_CURSOR_DIRECT
    if delivery_mode == DELIVERY_PROXY_ALIAS:
        key_hint = loan.alias_key_hint
    else:
        cred = session.get(AiAccountCredential, loan.credential_id)
        key_hint = cred.key_hint if cred else None
    return {
        "id": loan.id,
        "source_account_id": loan.source_account_id,
        "source_account_identifier": account.account_identifier if account else None,
        "primary_member_name": primary_member_name,
        "credential_id": loan.credential_id,
        "borrower_member_id": loan.borrower_member_id,
        "borrower_name": borrower_name,
        "baseline_used_cents": loan.baseline_used_cents,
        "borrowed_cents": borrowed_cents,
        "proxy_cost_cents": proxy_cost_cents,
        "status": loan.status,
        "auto_revoke_on_reset": loan.auto_revoke_on_reset,
        "loan_expires_on": deadline.isoformat() if deadline else None,
        "note": loan.note,
        "delivery_mode": delivery_mode,
        "key_hint": key_hint,
        "created_at": tool_datetime(loan.created_at),
        "revoked_at": tool_datetime(loan.revoked_at),
    }


def reveal_loan_user_key(loan: KeyLoan, encryption_key: str, session: Session) -> str:
    """返回借用人可见的 Key：proxy_alias → pka_；cursor_direct → cr*。"""
    from pulse.ingestion.crypto import decrypt_secret

    mode = getattr(loan, "delivery_mode", None) or DELIVERY_CURSOR_DIRECT
    if mode == DELIVERY_PROXY_ALIAS:
        if not loan.alias_encrypted_key:
            raise KeyLoanError("别名 Key 不可解密")
        try:
            return decrypt_secret(loan.alias_encrypted_key, encryption_key.strip())
        except Exception as exc:
            raise KeyLoanError("别名 Key 不可解密") from exc
    cred = session.get(AiAccountCredential, loan.credential_id)
    if not cred or not cred.encrypted_value:
        raise KeyLoanError("借用凭证不可解密")
    try:
        return CredentialService(session, encryption_key).decrypt_api_key(cred)
    except Exception as exc:
        raise KeyLoanError("借用凭证不可解密") from exc


def reveal_loan_cursor_key(loan: KeyLoan, encryption_key: str, session: Session) -> str:
    """管理员查看底层 Cursor Key（两种交付模式均可）。"""
    cred = session.get(AiAccountCredential, loan.credential_id)
    if not cred or not cred.encrypted_value:
        raise KeyLoanError("借用凭证不可解密")
    try:
        return CredentialService(session, encryption_key).decrypt_api_key(cred)
    except Exception as exc:
        raise KeyLoanError("借用凭证不可解密") from exc
