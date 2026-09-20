from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AiAccount, KeyLoan, Member
from pulse.tool_center.burn_rate import LenderCandidate, recommend_lenders
from pulse.tool_center.quota_reads import latest_snapshots_for_accounts
from pulse.tool_center.repository import ToolCenterRepository
from pulse.util.datetime_fmt import ensure_aware


def member_names_by_id(session: Session, member_ids: set[str]) -> dict[str, str]:
    """成员 id → 显示名；供候选构造填 primary_member_name（借用与代理池共用）。"""
    ids = {mid for mid in member_ids if mid}
    if not ids:
        return {}
    return {
        member.id: member.display_name
        for member in session.scalars(select(Member).where(Member.id.in_(ids)))
    }

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


def active_loan_counts_by_account(session: Session, team_id: str) -> dict[str, int]:
    rows = session.execute(
        select(KeyLoan.source_account_id, func.count())
        .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
        .where(AiAccount.team_id == team_id, KeyLoan.status == "active")
        .group_by(KeyLoan.source_account_id)
    ).all()
    return {account_id: count for account_id, count in rows}


def last_bound_at_by_account(
    session: Session, account_ids: list[str]
) -> dict[str, datetime]:
    """每个账号最近一次出借绑定时刻（驻留窗口基准）。

    取全部状态的借用记录：账号刚被切走（上一笔已回收）或刚被绑上（进行中）
    都算「刚动过」，都应进入驻留窗口。
    """
    if not account_ids:
        return {}
    rows = session.execute(
        select(KeyLoan.source_account_id, func.max(KeyLoan.source_bound_at))
        .where(
            KeyLoan.source_account_id.in_(account_ids),
            KeyLoan.source_bound_at.is_not(None),
        )
        .group_by(KeyLoan.source_account_id)
    ).all()
    out: dict[str, datetime] = {}
    for account_id, bound_at in rows:
        if bound_at is None:
            continue
        out[account_id] = ensure_aware(bound_at)  # type: ignore[assignment]
    return out


def build_lender_candidates(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
) -> list[LenderCandidate]:
    """组装出借候选：最新快照 + renews_on + 当前在借人数 + 人工分 + 驻留。"""
    exclude_account_ids = exclude_account_ids or set()
    repo = ToolCenterRepository(session, team_id)
    accounts = [
        account
        for account in repo.list_active_accounts(vendor_slug="cursor")
        if account.id not in exclude_account_ids
    ]
    snapshots = latest_snapshots_for_accounts(session, [account.id for account in accounts])
    loan_counts = active_loan_counts_by_account(session, team_id)
    accounts = [account for account in accounts if snapshots.get(account.id)]
    bound_at_by_account = last_bound_at_by_account(
        session, [account.id for account in accounts]
    )
    primary_ids = {a.primary_member_id for a in accounts if a.primary_member_id}
    member_names = member_names_by_id(session, primary_ids)
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
                score_adjust=account.proxy_score_adjust,
                reserve_pct=account.proxy_reserve_pct,
                bound_at=bound_at_by_account.get(account.id),
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
    pool: str | None = None,
) -> dict | None:
    """纯确定性打分选号（不调用 Jev）。

    生产路径（管理员发放 / 自助借用 / 定期重评）走
    :func:`pulse.tool_center.key_loan_auto.resolve_auto_lender`，它在这之上叠加
    Jev 主判与护栏。本函数保留为「只看算法分」的入口，供回测、对照与工具使用。
    """
    candidates = build_lender_candidates(
        session, team_id, exclude_account_ids=exclude_account_ids
    )
    ranked = recommend_lenders(
        candidates, today, loan_selection=loan_selection, pool=pool
    )
    return ranked[0] if ranked else None

