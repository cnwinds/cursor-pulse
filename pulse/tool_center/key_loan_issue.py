from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.ingestion.credentials import CredentialService
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount, AiAccountCredential, KeyLoan, Member
from pulse.tool_center.account_pick import filter_cursor_accounts
from pulse.tool_center.burn_rate import analyze_burn_rate
from pulse.tool_center.key_loan_borrower import (
    borrower_unbound_cursor_accounts,
    ensure_borrower_has_cursor_key,
)
from pulse.tool_center.key_loan_delivery import (
    DELIVERY_PROXY_ALIAS,
    VALID_DELIVERY_MODES,
    KeyLoanError,
)
from pulse.tool_center.key_loan_lender import (
    account_loan_deadline,
    recommend_lender_for_borrower,
)
from pulse.tool_center.key_loan_store import KeyLoanService
from pulse.tool_center.quota_reads import latest_snapshots_for_team
from pulse.tool_center.repository import ToolCenterRepository
from pulse.util.datetime_fmt import tool_datetime

logger = logging.getLogger(__name__)


def _resolve_cursor_client(cursor_client: CursorApiClient | None) -> CursorApiClient:
    """Prefer explicit client; otherwise construct via key_loan_store for patch locality."""
    if cursor_client is not None:
        return cursor_client
    from pulse.tool_center import key_loan_store as store

    return store.CursorApiClient()

def _lock_account_for_loan_issue(session: Session, account_id: str) -> None:
    """串行化同一账号的并发发放。

    Postgres：SELECT ... FOR UPDATE 锁账号行；
    sqlite：一次 no-op 写操作抢占写锁（依赖 pysqlite legacy 模式下先前 SELECT
    不持读快照、首个写操作才升级锁的驱动行为），并发写者在 busy timeout 内排队，
    超时未获锁转为业务可读错误。
    锁持有至事务提交（覆盖后续远端 API 调用），调用方应尽快提交。
    """
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                select(AiAccount.id).where(AiAccount.id == account_id).with_for_update()
            )
        else:
            session.execute(
                update(AiAccount)
                .where(AiAccount.id == account_id)
                .values(updated_at=AiAccount.updated_at)
            )
    except OperationalError as exc:
        raise KeyLoanError("系统繁忙，请稍后重试") from exc


def _lock_member_for_self_loan(session: Session, member_id: str) -> None:
    """串行化同一借用人的并发自助申请（机制同 _lock_account_for_loan_issue）。"""
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                select(Member.id).where(Member.id == member_id).with_for_update()
            )
        else:
            session.execute(
                update(Member).where(Member.id == member_id).values(id=Member.id)
            )
    except OperationalError as exc:
        raise KeyLoanError("系统繁忙，请稍后重试") from exc


def _lock_loan_for_update(session: Session, loan_id: str) -> None:
    """串行化同一借用记录的并发换绑/修改（机制同 _lock_account_for_loan_issue）。"""
    try:
        if session.get_bind().dialect.name == "postgresql":
            session.execute(
                select(KeyLoan.id).where(KeyLoan.id == loan_id).with_for_update()
            )
        else:
            session.execute(
                update(KeyLoan).where(KeyLoan.id == loan_id).values(id=KeyLoan.id)
            )
    except OperationalError as exc:
        raise KeyLoanError("系统繁忙，请稍后重试") from exc


def _resolve_remote_key_id(
    cursor_client: CursorApiClient,
    token: str,
    *,
    key_name: str,
    api_key: str,
) -> int | None:
    keys = cursor_client.list_user_api_keys(token, api_key=api_key)
    for item in keys:
        if item.get("name") == key_name:
            return int(item["id"])
    if keys:
        return int(keys[-1]["id"])
    return None

def issue_loan_key(
    session: Session,
    encryption_key: str,
    *,
    team_id: str,
    source_account_id: str,
    borrower_member_id: str,
    bound_by_member_id: str,
    note: str | None = None,
    auto_revoke_on_reset: bool = True,
    key_name: str | None = None,
    delivery_mode: str = DELIVERY_PROXY_ALIAS,
    cursor_client: CursorApiClient | None = None,
    loan_selection: LoanSelectionConfig | None = None,
) -> dict:
    mode = (delivery_mode or DELIVERY_PROXY_ALIAS).strip()
    if mode != DELIVERY_PROXY_ALIAS:
        raise KeyLoanError("仅支持代理别名 Key（proxy_alias）交付；Cursor Key 直发已停用")

    repo = ToolCenterRepository(session, team_id)
    account = repo.get_account(source_account_id)
    if not account or account.team_id != team_id:
        raise KeyLoanError("借出账号不存在")
    if not account.vendor or account.vendor.slug != "cursor":
        raise KeyLoanError("仅 Cursor 账号支持 Key 调配")

    borrower = session.get(Member, borrower_member_id)
    if not borrower or borrower.team_id != team_id:
        raise KeyLoanError("借用人不存在")

    ensure_borrower_has_cursor_key(
        session, team_id, borrower_member_id, for_admin=True
    )

    client = _resolve_cursor_client(cursor_client)
    cred_service = CredentialService(session, encryption_key, cursor_client=client)
    primary = cred_service.get_primary_credential(source_account_id)
    if not primary:
        raise KeyLoanError("借出账号未绑定主 API Key，请联系管理员")

    loan_svc = KeyLoanService(session, encryption_key, cursor_client=client)
    snapshot = loan_svc.latest_snapshot(source_account_id)
    if not snapshot:
        raise KeyLoanError("借出账号暂无额度快照，请联系管理员先同步")
    analysis = analyze_burn_rate(snapshot)
    if analysis.status == "exhausted":
        raise KeyLoanError("借出账号套内额度已耗尽，请稍后再试或联系管理员")

    selection = loan_selection or LoanSelectionConfig()
    _lock_account_for_loan_issue(session, source_account_id)
    active_loan_count = (
        session.scalar(
            select(func.count(KeyLoan.id)).where(
                KeyLoan.source_account_id == source_account_id,
                KeyLoan.status == "active",
            )
        )
        or 0
    )
    if active_loan_count >= selection.max_active_loans_per_account:
        raise KeyLoanError("该账号借用名额已满，请选择其他账号")

    borrower_name = borrower.display_name.replace(" ", "-")
    resolved_key_name = key_name or f"pulse-loan-{borrower_name}"

    primary_api_key = cred_service.decrypt_api_key(primary)
    token = client.get_access_token(primary_api_key)
    created = client.create_user_api_key(token, resolved_key_name, api_key=primary_api_key)
    loan_api_key = created.get("apiKey")
    if not loan_api_key:
        raise KeyLoanError("CreateUserApiKey 未返回 apiKey")

    remote_id = _resolve_remote_key_id(
        client, token, key_name=resolved_key_name, api_key=primary_api_key
    )
    loan_cred = cred_service.create_loan_credential(
        account_id=source_account_id,
        api_key=loan_api_key,
        display_name=resolved_key_name,
        remote_key_id=remote_id,
        assignee_member_id=borrower_member_id,
        bound_by_member_id=bound_by_member_id,
    )

    from pulse.ingestion.crypto import encrypt_secret
    from pulse.proxy.keys import generate_alias_key

    alias_plaintext, alias_key_hash, alias_key_hint = generate_alias_key()
    if not (encryption_key or "").strip():
        raise KeyLoanError("未配置凭证加密密钥，无法签发代理别名 Key")
    alias_encrypted_key = encrypt_secret(alias_plaintext, encryption_key.strip())

    deadline = account_loan_deadline(account) if auto_revoke_on_reset else None
    loan = loan_svc.create_loan_record(
        source_account_id=source_account_id,
        credential_id=loan_cred.id,
        borrower_member_id=borrower_member_id,
        baseline_used_cents=snapshot.used_cents,
        auto_revoke_on_reset=auto_revoke_on_reset,
        expires_on=deadline,
        note=note,
        delivery_mode=DELIVERY_PROXY_ALIAS,
        alias_key_hash=alias_key_hash,
        alias_key_hint=alias_key_hint,
        alias_encrypted_key=alias_encrypted_key,
    )
    primary_member_name = None
    if account.primary_member_id:
        primary = session.get(Member, account.primary_member_id)
        primary_member_name = primary.display_name if primary else None
    warning = (
        "此为代理别名 Key（pka_），须配置 HTTPS_PROXY 后使用。"
        "可随时发送「我的借用」再次查看。借用消耗为账号用量差值近似，非精确按 Key 统计。"
    )
    return {
        "loan_id": loan.id,
        "api_key": alias_plaintext,
        "key_hint": alias_key_hint,
        "delivery_mode": DELIVERY_PROXY_ALIAS,
        "borrower_member_id": borrower.id,
        "borrower_name": borrower.display_name,
        "source_account_identifier": account.account_identifier,
        "primary_member_name": primary_member_name,
        "loan_expires_on": deadline.isoformat() if deadline else None,
        "warning": warning,
    }


def reassign_loan_source(
    session: Session,
    encryption_key: str,
    *,
    team_id: str,
    loan_id: str,
    new_source_account_id: str,
    bound_by_member_id: str,
    cursor_client: CursorApiClient | None = None,
    loan_selection: LoanSelectionConfig | None = None,
) -> dict:
    """更换出借账号，保持同一 pka_ / loan id；新建远端 Key。

    远端撤销旧 Key 延后到 DB commit 之后，由
    :func:`finalize_reassign_old_remote_revoke` 执行，避免 commit 失败时
    旧 Key 已在 Cursor 侧被吊销而本地仍指向旧 credential。
    """
    loan_svc = KeyLoanService(session, encryption_key, cursor_client=cursor_client)
    _lock_loan_for_update(session, loan_id)
    loan = loan_svc.get_loan(loan_id)
    if not loan:
        raise KeyLoanError("借用记录不存在")
    if loan.status != "active":
        raise KeyLoanError("仅进行中的借用可更换出借账号")
    mode = getattr(loan, "delivery_mode", None) or DELIVERY_CURSOR_DIRECT
    if mode != DELIVERY_PROXY_ALIAS:
        raise KeyLoanError("仅代理别名 Key 支持更换出借账号")
    if not loan.alias_key_hash or not loan.alias_encrypted_key:
        raise KeyLoanError("别名 Key 缺失，无法安全换绑")
    if loan.source_account_id == new_source_account_id:
        raise KeyLoanError("新出借账号与当前相同")

    repo = ToolCenterRepository(session, team_id)
    old_account = repo.get_account(loan.source_account_id)
    new_account = repo.get_account(new_source_account_id)
    if not new_account or new_account.team_id != team_id:
        raise KeyLoanError("新出借账号不存在")
    if not new_account.vendor or new_account.vendor.slug != "cursor":
        raise KeyLoanError("仅 Cursor 账号支持 Key 调配")

    client = _resolve_cursor_client(cursor_client)
    cred_service = CredentialService(session, encryption_key, cursor_client=client)
    primary = cred_service.get_primary_credential(new_source_account_id)
    if not primary:
        raise KeyLoanError("新出借账号未绑定主 API Key，请联系管理员")

    snapshot = loan_svc.latest_snapshot(new_source_account_id)
    if not snapshot:
        raise KeyLoanError("新出借账号暂无额度快照，请联系管理员先同步")
    if analyze_burn_rate(snapshot).status == "exhausted":
        raise KeyLoanError("新出借账号套内额度已耗尽，请选择其他账号")

    selection = loan_selection or LoanSelectionConfig()
    _lock_account_for_loan_issue(session, new_source_account_id)
    active_loan_count = (
        session.scalar(
            select(func.count(KeyLoan.id)).where(
                KeyLoan.source_account_id == new_source_account_id,
                KeyLoan.status == "active",
            )
        )
        or 0
    )
    if active_loan_count >= selection.max_active_loans_per_account:
        raise KeyLoanError("新出借账号借用名额已满，请选择其他账号")

    borrower = session.get(Member, loan.borrower_member_id) if loan.borrower_member_id else None
    borrower_name = (borrower.display_name if borrower else "loan").replace(" ", "-")
    resolved_key_name = f"pulse-loan-{borrower_name}"

    primary_api_key = cred_service.decrypt_api_key(primary)
    token = client.get_access_token(primary_api_key)
    created = client.create_user_api_key(token, resolved_key_name, api_key=primary_api_key)
    loan_api_key = created.get("apiKey")
    if not loan_api_key:
        raise KeyLoanError("CreateUserApiKey 未返回 apiKey")

    remote_id = _resolve_remote_key_id(
        client, token, key_name=resolved_key_name, api_key=primary_api_key
    )
    new_cred = cred_service.create_loan_credential(
        account_id=new_source_account_id,
        api_key=loan_api_key,
        display_name=resolved_key_name,
        remote_key_id=remote_id,
        assignee_member_id=loan.borrower_member_id,
        bound_by_member_id=bound_by_member_id,
    )

    old_source_id = loan.source_account_id
    old_cred_id = loan.credential_id
    old_identifier = old_account.account_identifier if old_account else None

    deadline = (
        account_loan_deadline(new_account) if loan.auto_revoke_on_reset else None
    )
    loan.source_account_id = new_source_account_id
    loan.credential_id = new_cred.id
    loan.baseline_used_cents = snapshot.used_cents
    loan.expires_on = deadline

    pending_remote_revoke = None
    old_cred = session.get(AiAccountCredential, old_cred_id)
    if old_cred:
        if old_cred.remote_key_id and old_cred.status == "active":
            pending_remote_revoke = {
                "old_source_account_id": old_source_id,
                "old_cred_id": old_cred_id,
                "remote_key_id": old_cred.remote_key_id,
            }
        old_cred.status = "revoked"
        old_cred.sync_enabled = False
        old_cred.encrypted_value = ""
    session.flush()

    return {
        "loan_id": loan.id,
        "delivery_mode": DELIVERY_PROXY_ALIAS,
        "borrower_member_id": loan.borrower_member_id,
        "borrower_name": borrower.display_name if borrower else None,
        "old_source_account_id": old_source_id,
        "old_source_account_identifier": old_identifier,
        "source_account_id": new_account.id,
        "source_account_identifier": new_account.account_identifier,
        "loan_expires_on": deadline.isoformat() if deadline else None,
        "alias_key_hint": loan.alias_key_hint,
        "old_remote_revoked": False,
        "key_hint": loan.alias_key_hint,
        "_pending_old_remote_revoke": pending_remote_revoke,
    }


def finalize_reassign_old_remote_revoke(
    session: Session,
    encryption_key: str,
    result: dict,
    *,
    cursor_client: CursorApiClient | None = None,
) -> bool:
    """Best-effort revoke of the previous Cursor key after DB commit.

    Mutates ``result``: pops ``_pending_old_remote_revoke``, sets
    ``old_remote_revoked``.
    """
    pending = result.pop("_pending_old_remote_revoke", None)
    if not pending:
        result["old_remote_revoked"] = False
        return False

    client = _resolve_cursor_client(cursor_client)
    cred_service = CredentialService(session, encryption_key, cursor_client=client)
    try:
        old_primary = cred_service.get_primary_credential(
            pending["old_source_account_id"]
        )
        if not old_primary:
            logger.warning(
                "reassign loan %s: no primary on old source, skip remote revoke",
                result.get("loan_id"),
            )
            result["old_remote_revoked"] = False
            return False
        old_api_key = cred_service.decrypt_api_key(old_primary)
        old_token = client.get_access_token(old_api_key)
        client.revoke_user_api_key(
            old_token, pending["remote_key_id"], api_key=old_api_key
        )
        result["old_remote_revoked"] = True
        return True
    except Exception:
        logger.warning(
            "reassign loan %s: failed to revoke old remote key",
            result.get("loan_id"),
            exc_info=True,
        )
        result["old_remote_revoked"] = False
        return False


def request_self_service_loan(
    session: Session,
    encryption_key: str,
    *,
    team_id: str,
    borrower: Member,
    note: str | None = None,
    bound_by_member_id: str | None = None,
    cursor_client: CursorApiClient | None = None,
    loan_selection: LoanSelectionConfig | None = None,
) -> dict:
    if borrower.status != "active":
        raise KeyLoanError("成员状态不可用，请联系管理员")

    _lock_member_for_self_loan(session, borrower.id)
    loan_svc = KeyLoanService(session, encryption_key, cursor_client=cursor_client)
    if loan_svc.active_loan_for_borrower(borrower.id):
        raise KeyLoanError("你已有进行中的借用，请先发送「归还 Key」后再申请")

    repo = ToolCenterRepository(session, team_id)
    own_accounts = filter_cursor_accounts(repo.get_primary_accounts_for_member(borrower.id))
    if not own_accounts:
        raise KeyLoanError(
            "你尚未分配 Cursor 账号。请联系管理员在台账中分配账号。"
        )

    ensure_borrower_has_cursor_key(session, team_id, borrower.id)

    snapshots = latest_snapshots_for_team(session, team_id)
    own_needs_loan = False
    for account in own_accounts:
        snap = snapshots.get(account.id)
        if not snap:
            continue
        status = analyze_burn_rate(snap).status
        if status in ("warning", "exhausted"):
            own_needs_loan = True
            break

    if not own_needs_loan:
        raise KeyLoanError(
            "你名下账号额度尚充足，暂不支持自助借 Key。"
            "若确有紧急需求，请联系管理员在额度看板分配。"
        )

    lender = recommend_lender_for_borrower(
        session,
        team_id,
        exclude_account_ids={a.id for a in own_accounts},
        loan_selection=loan_selection,
    )
    if not lender:
        raise KeyLoanError("当前没有可借出的富余账号，请联系管理员")

    return issue_loan_key(
        session,
        encryption_key,
        team_id=team_id,
        source_account_id=lender["account_id"],
        borrower_member_id=borrower.id,
        bound_by_member_id=bound_by_member_id or borrower.id,
        note=note or "自助借 Key",
        auto_revoke_on_reset=True,
        delivery_mode=DELIVERY_PROXY_ALIAS,
        cursor_client=cursor_client,
        loan_selection=loan_selection,
    )

