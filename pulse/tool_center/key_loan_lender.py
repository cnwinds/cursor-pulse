from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AiAccount, KeyLoan, Member
from pulse.tool_center.burn_rate import LenderCandidate, recommend_lenders
from pulse.tool_center.quota_reads import latest_snapshots_for_accounts
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


def sticky_assignment_for_borrower(
    session: Session, borrower_member_id: str | None
) -> tuple[str | None, datetime | None]:
    """Most recent active Key Loan for Switch Cooldown.

    Returns ``(source_account_id, created_at)`` or ``(None, None)``.
    """
    if not borrower_member_id:
        return None, None
    loan = session.scalar(
        select(KeyLoan)
        .where(
            KeyLoan.borrower_member_id == borrower_member_id,
            KeyLoan.status == "active",
        )
        .order_by(KeyLoan.created_at.desc())
        .limit(1)
    )
    if loan is None:
        return None, None
    return loan.source_account_id, loan.created_at


def active_loan_counts_by_account(session: Session, team_id: str) -> dict[str, int]:
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
    repo = ToolCenterRepository(session, team_id)
    accounts = [
        account
        for account in repo.list_active_accounts(vendor_slug="cursor")
        if account.id not in exclude_account_ids
    ]
    snapshots = latest_snapshots_for_accounts(session, [account.id for account in accounts])
    loan_counts = active_loan_counts_by_account(session, team_id)
    accounts = [account for account in accounts if snapshots.get(account.id)]
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
                score_adjust=account.proxy_score_adjust,
            )
        )
    return candidates


def _jev_scores_for_ranked(config, ranked: list[dict], quota_pool: str | None) -> dict[str, float]:
    from pulse.llm.jev import build_jev_client
    from pulse.tool_center.jev_rank import score_assignment_candidates

    if not ranked or config is None:
        return {}
    client = build_jev_client(config)
    if client is None:
        return {}
    selection = getattr(getattr(config, "tool_center", None), "loan_selection", None)
    min_conf = 0.35 if selection is None else selection.jev_min_confidence
    scored = score_assignment_candidates(
        client, ranked, quota_pool=quota_pool, min_confidence=min_conf
    )
    return {account_id: item.blend for account_id, item in scored.items()}


def rank_lenders_for_assignment(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
    today: date | None = None,
    now: datetime | None = None,
    loan_selection: LoanSelectionConfig | None = None,
    quota_pool: str | None = None,
    config=None,
    sticky_account_id: str | None = None,
    sticky_since: datetime | None = None,
    exclude_at_loan_cap: bool | None = False,
    use_jev: bool = True,
) -> list[dict]:
    """Rank lenders for Key Loan assignment (optional Jev blend)."""
    from pulse.tool_center.snapshot_headroom import normalize_quota_pool

    pool = normalize_quota_pool(quota_pool)
    candidates = build_lender_candidates(
        session, team_id, exclude_account_ids=exclude_account_ids
    )
    ranked = recommend_lenders(
        candidates,
        today,
        now=now,
        loan_selection=loan_selection,
        quota_pool=pool,
        exclude_at_loan_cap=exclude_at_loan_cap,
        sticky_account_id=sticky_account_id,
        sticky_since=sticky_since,
    )
    if not use_jev or not ranked:
        return ranked
    jev_scores = _jev_scores_for_ranked(config, ranked, pool)
    if not jev_scores:
        return ranked
    return recommend_lenders(
        candidates,
        today,
        now=now,
        loan_selection=loan_selection,
        quota_pool=pool,
        jev_scores=jev_scores,
        exclude_at_loan_cap=exclude_at_loan_cap,
        sticky_account_id=sticky_account_id,
        sticky_since=sticky_since,
    )


def recommend_lender_for_borrower(
    session: Session,
    team_id: str,
    *,
    exclude_account_ids: set[str] | None = None,
    today: date | None = None,
    now: datetime | None = None,
    loan_selection: LoanSelectionConfig | None = None,
    quota_pool: str | None = None,
    config=None,
    sticky_account_id: str | None = None,
    sticky_since: datetime | None = None,
    borrower_member_id: str | None = None,
) -> dict | None:
    if sticky_account_id is None and borrower_member_id:
        sticky_account_id, sticky_since = sticky_assignment_for_borrower(
            session, borrower_member_id
        )
    ranked = rank_lenders_for_assignment(
        session,
        team_id,
        exclude_account_ids=exclude_account_ids,
        today=today,
        now=now,
        loan_selection=loan_selection,
        quota_pool=quota_pool,
        config=config,
        sticky_account_id=sticky_account_id,
        sticky_since=sticky_since,
        exclude_at_loan_cap=None,
        use_jev=True,
    )
    return ranked[0] if ranked else None

