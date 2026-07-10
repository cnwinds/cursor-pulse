from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.periods import previous_period
from pulse.storage.models import AiAccount, Member, UsageSummary


def _effective_quota_ratio(summary: UsageSummary) -> float | None:
    if summary.cycle_quota_usage_ratio is not None:
        return float(summary.cycle_quota_usage_ratio)
    if summary.quota_usage_ratio is not None:
        return float(summary.quota_usage_ratio)
    return None


def evaluate_account_upgrade(session: Session, account_id: str, period: str) -> bool:
    """连续 N 月额度使用率均达阈值时，首次标记 suggest_dedicated。"""
    account = session.scalar(
        select(AiAccount)
        .options(joinedload(AiAccount.plan))
        .where(AiAccount.id == account_id)
    )
    if not account or not account.plan or not account.plan.quota_ratio_enabled:
        return False
    if account.suggest_dedicated:
        return False

    plan = account.plan
    threshold = float(plan.upgrade_threshold_pct or 95)
    months = int(plan.upgrade_consecutive_months or 2)

    periods = [period]
    for _ in range(months - 1):
        periods.append(previous_period(periods[-1]))

    summaries = list(
        session.scalars(
            select(UsageSummary).where(
                UsageSummary.account_id == account_id,
                UsageSummary.period.in_(periods),
            )
        )
    )
    by_period = {s.period: s for s in summaries}
    if len(by_period) < months:
        return False

    for p in periods:
        summary = by_period.get(p)
        if summary is None:
            return False
        ratio = _effective_quota_ratio(summary)
        if ratio is None:
            return False
        if ratio < threshold:
            return False

    account.suggest_dedicated = True
    session.flush()
    return True


def notify_upgrade_if_needed(
    session: Session,
    account_id: str,
    period: str,
    *,
    send_private_message,
    admin_ids: list[str],
) -> bool:
    """检查升级条件并通知主管与管理员（仅首次触发）。"""
    from pulse.tool_center.briefing import format_upgrade_alert

    triggered = evaluate_account_upgrade(session, account_id, period)
    if not triggered:
        return False

    account = session.scalar(
        select(AiAccount).options(joinedload(AiAccount.plan)).where(AiAccount.id == account_id)
    )
    if not account:
        return False

    text = format_upgrade_alert(session, account, period)
    notified: set[str] = set()

    if account.primary_member_id:
        primary = session.get(Member, account.primary_member_id)
        if primary and primary.manager_member_id:
            manager = session.get(Member, primary.manager_member_id)
            if manager and manager.dingtalk_user_id not in notified:
                send_private_message(manager.dingtalk_user_id, text)
                notified.add(manager.dingtalk_user_id)

    for admin_id in admin_ids:
        if admin_id not in notified:
            send_private_message(admin_id, text)
            notified.add(admin_id)

    return True
