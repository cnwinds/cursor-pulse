from __future__ import annotations

from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.tool_center.account_pick import filter_cursor_accounts
from pulse.tool_center.key_loan_delivery import KeyLoanError
from pulse.tool_center.repository import ToolCenterRepository

def _borrower_cursor_key_required_message(
    *,
    for_admin: bool = False,
    unbound_accounts: list[str] | None = None,
) -> str:
    count = len(unbound_accounts) if unbound_accounts else 0
    if for_admin:
        if count:
            return f"借用人还有 {count} 个 Cursor 账号未绑 Key，请先通知其完成绑定。"
        return "借用人名下 Cursor 账号未全部绑定 Key。"
    if count:
        return (
            f"你还有 {count} 个 Cursor 账号未绑 Key，请先绑定后再申请。\n"
            "发送：绑定 cursor 你的邮箱@c.com crsr_..."
        )
    return (
        "请先为名下每个 Cursor 账号绑定 Key。\n"
        "发送：绑定 cursor 你的邮箱@c.com crsr_..."
    )


def borrower_unbound_cursor_accounts(
    session: Session,
    team_id: str,
    borrower_member_id: str,
) -> list[str]:
    repo = ToolCenterRepository(session, team_id)
    cred_service = CredentialService(session, encryption_key="")
    unbound: list[str] = []
    for account in filter_cursor_accounts(repo.get_primary_accounts_for_member(borrower_member_id)):
        cred = cred_service.get_primary_credential(account.id)
        if cred and cred.encrypted_value:
            continue
        label = account.account_identifier or account.id[:8]
        unbound.append(label)
    return unbound


def borrower_has_bound_cursor_key(
    session: Session,
    team_id: str,
    borrower_member_id: str,
) -> bool:
    accounts = filter_cursor_accounts(
        ToolCenterRepository(session, team_id).get_primary_accounts_for_member(
            borrower_member_id
        )
    )
    if not accounts:
        return False
    return not borrower_unbound_cursor_accounts(session, team_id, borrower_member_id)


def ensure_borrower_has_cursor_key(
    session: Session,
    team_id: str,
    borrower_member_id: str,
    *,
    for_admin: bool = False,
) -> None:
    unbound = borrower_unbound_cursor_accounts(session, team_id, borrower_member_id)
    if unbound:
        raise KeyLoanError(
            _borrower_cursor_key_required_message(
                for_admin=for_admin,
                unbound_accounts=unbound,
            )
        )

