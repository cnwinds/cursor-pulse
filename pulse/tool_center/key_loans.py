from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.ingestion.credentials import CredentialService
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import (
    AccountQuotaSnapshot,
    AiAccount,
    AiAccountCredential,
    KeyLoan,
    Member,
)
from pulse.tool_center.account_pick import filter_cursor_accounts
from pulse.tool_center.burn_rate import (
    LenderCandidate,
    analyze_burn_rate,
    recommend_lenders,
)
from pulse.proxy.service import loan_proxy_totals
from pulse.tool_center.quota_reads import (
    latest_snapshots_for_accounts,
    latest_snapshots_for_team,
)
from pulse.tool_center.repository import ToolCenterRepository
from pulse.util.datetime_fmt import tool_datetime

logger = logging.getLogger(__name__)


DELIVERY_CURSOR_DIRECT = "cursor_direct"
DELIVERY_PROXY_ALIAS = "proxy_alias"
VALID_DELIVERY_MODES = frozenset({DELIVERY_CURSOR_DIRECT, DELIVERY_PROXY_ALIAS})


class KeyLoanError(ValueError):
    pass


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


class KeyLoanService:
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


def account_loan_deadline(account: AiAccount) -> date | None:
    """账号上借用 key 的自动回收日：额度重置日与订阅到期日取先到者。

    发放时冻结到 KeyLoan.expires_on；展示优先读冻结值。打分侧见
    burn_rate.lender_deadline（数据源快照 cycle_end，
    与 usage_resets_on 同源自 Cursor billingCycleEnd）。
    """
    deadline = account.usage_resets_on
    if account.renews_on and (deadline is None or account.renews_on < deadline):
        deadline = account.renews_on
    return deadline


def _loan_created_date(loan: KeyLoan) -> date:
    created = loan.created_at
    if created.tzinfo is not None:
        return created.astimezone(timezone.utc).date()
    return created.date()


def loan_display_expires_on(loan: KeyLoan, account: AiAccount | None) -> date | None:
    """UI/API 展示用回收日：优先冻结值，否则回退账号当前 deadline。"""
    if loan.expires_on is not None:
        return loan.expires_on
    if account is None:
        return None
    return account_loan_deadline(account)


def _active_loan_counts_by_account(session: Session, team_id: str) -> dict[str, int]:
    rows = session.execute(
        select(KeyLoan.source_account_id, func.count())
        .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
        .where(AiAccount.team_id == team_id, KeyLoan.status == "active")
        .group_by(KeyLoan.source_account_id)
    ).all()
    return {account_id: count for account_id, count in rows}


def build_lender_candidates(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
) -> list[LenderCandidate]:
    """组装出借候选：最新快照 + renews_on + 当前在借人数。"""
    exclude_account_ids = exclude_account_ids or set()
    snapshots = latest_snapshots_for_team(session, team_id)
    loan_counts = _active_loan_counts_by_account(session, team_id)
    repo = ToolCenterRepository(session, team_id)
    accounts = [
        account
        for account in repo.list_active_accounts()
        if account.vendor
        and account.vendor.slug == "cursor"
        and account.id not in exclude_account_ids
        and snapshots.get(account.id)
    ]
    primary_ids = {a.primary_member_id for a in accounts if a.primary_member_id}
    member_names: dict[str, str] = {}
    if primary_ids:
        member_names = {
            m.id: m.display_name
            for m in session.scalars(select(Member).where(Member.id.in_(primary_ids)))
        }
    candidates: list[LenderCandidate] = []
    for account in accounts:
        snap = snapshots[account.id]
        primary_name = None
        if account.primary_member_id:
            primary_name = member_names.get(account.primary_member_id)
        candidates.append(
            LenderCandidate(
                snapshot=snap,
                account_id=account.id,
                account_identifier=account.account_identifier,
                renews_on=account.renews_on,
                active_loans=loan_counts.get(account.id, 0),
                primary_member_name=primary_name,
            )
        )
    return candidates


def recommend_lender_for_borrower(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
    today: date | None = None,
    loan_selection: LoanSelectionConfig | None = None,
) -> dict | None:
    candidates = build_lender_candidates(
        session, team_id, exclude_account_ids=exclude_account_ids
    )
    ranked = recommend_lenders(candidates, today, loan_selection=loan_selection)
    return ranked[0] if ranked else None


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

    client = cursor_client or CursorApiClient()
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

    client = cursor_client or CursorApiClient()
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

    client = cursor_client or CursorApiClient()
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
