from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AiAccount, KeyLoan, Member
from pulse.tool_center.burn_rate import LenderCandidate, recommend_lenders
from pulse.tool_center.quota_reads import latest_snapshots_for_team
from pulse.tool_center.repository import ToolCenterRepository

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

