from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.proxy.usage_queries import loan_proxy_totals_by_loan
from pulse.storage.models import AiAccount, AiAccountCredential, KeyLoan, Member
from pulse.tool_center.key_loan_delivery import (
    DELIVERY_CURSOR_DIRECT,
    DELIVERY_PROXY_ALIAS,
    KeyLoanError,
)
from pulse.tool_center.key_loan_lender import loan_display_expires_on
from pulse.tool_center.quota_reads import latest_snapshots_for_accounts
from pulse.util.datetime_fmt import tool_datetime


def loan_payloads(loans: list[KeyLoan], session: Session) -> list[dict]:
    if not loans:
        return []
    borrower_ids = {loan.borrower_member_id for loan in loans if loan.borrower_member_id}
    account_ids = {loan.source_account_id for loan in loans}
    accounts = {
        account.id: account
        for account in session.scalars(select(AiAccount).where(AiAccount.id.in_(account_ids)))
    }
    primary_ids = {
        account.primary_member_id
        for account in accounts.values()
        if account.primary_member_id
    }
    member_ids = borrower_ids | primary_ids
    members = {
        member.id: member
        for member in (
            session.scalars(select(Member).where(Member.id.in_(member_ids))).all()
            if member_ids
            else []
        )
    }
    snapshots = latest_snapshots_for_accounts(session, account_ids)
    proxy_totals = loan_proxy_totals_by_loan(session, [loan.id for loan in loans])
    cred_ids = {
        loan.credential_id
        for loan in loans
        if (getattr(loan, "delivery_mode", None) or DELIVERY_CURSOR_DIRECT)
        != DELIVERY_PROXY_ALIAS
        and loan.credential_id
    }
    credentials = {
        cred.id: cred
        for cred in (
            session.scalars(
                select(AiAccountCredential).where(AiAccountCredential.id.in_(cred_ids))
            ).all()
            if cred_ids
            else []
        )
    }
    payloads = []
    for loan in loans:
        account = accounts.get(loan.source_account_id)
        borrower = members.get(loan.borrower_member_id) if loan.borrower_member_id else None
        primary = (
            members.get(account.primary_member_id)
            if account and account.primary_member_id
            else None
        )
        used_cents = snapshots[loan.source_account_id].used_cents if loan.source_account_id in snapshots else 0
        borrowed_cents = max(used_cents - loan.baseline_used_cents, 0)
        deadline = loan_display_expires_on(loan, account)
        _, proxy_cost_cents = proxy_totals.get(loan.id, (0, 0))
        delivery_mode = getattr(loan, "delivery_mode", None) or DELIVERY_CURSOR_DIRECT
        if delivery_mode == DELIVERY_PROXY_ALIAS:
            key_hint = loan.alias_key_hint
        else:
            cred = credentials.get(loan.credential_id)
            key_hint = cred.key_hint if cred else None
        payloads.append(
            {
                "id": loan.id,
                "source_account_id": loan.source_account_id,
                "source_account_identifier": account.account_identifier if account else None,
                "primary_member_name": primary.display_name if primary else None,
                "credential_id": loan.credential_id,
                "borrower_member_id": loan.borrower_member_id,
                "borrower_name": borrower.display_name if borrower else None,
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
        )
    return payloads


def loan_payload(loan: KeyLoan, session: Session) -> dict:
    return loan_payloads([loan], session)[0]


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
