from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.periods import pct_change, previous_period
from pulse.storage.models import AiAccount, UsageSummary


def aggregate_account_metrics(session: Session, period: str, *, team_id: str) -> dict:
    """从 usage_summaries 聚合账号维度指标（分厂家/币种，匿名模型族）。"""
    accounts = list(
        session.scalars(
            select(AiAccount)
            .options(joinedload(AiAccount.plan), joinedload(AiAccount.vendor))
            .where(AiAccount.team_id == team_id)
        )
    )
    active = [a for a in accounts if a.status in {"trial", "shared", "dedicated"}]

    summaries = list(
        session.scalars(
            select(UsageSummary)
            .join(AiAccount, UsageSummary.account_id == AiAccount.id)
            .where(AiAccount.team_id == team_id, UsageSummary.period == period)
        )
    )
    summary_by_account = {s.account_id: s for s in summaries}

    by_vendor_currency: dict[str, dict] = {}
    model_family_totals: dict[str, float] = defaultdict(float)
    account_rows: list[dict] = []
    suggest_count = 0

    for account in active:
        summary = summary_by_account.get(account.id)
        if account.suggest_dedicated:
            suggest_count += 1
        if not summary:
            continue

        vendor_name = account.vendor.name if account.vendor else "Unknown"
        currency = (summary.primary_metric_unit or "usd").upper()
        bucket_key = f"{vendor_name}|{currency}"
        bucket = by_vendor_currency.setdefault(
            bucket_key,
            {
                "vendor_name": vendor_name,
                "currency": currency,
                "total_usage": 0.0,
                "account_count": 0,
                "avg_quota_ratio": None,
                "_ratio_sum": 0.0,
                "_ratio_count": 0,
            },
        )
        value = float(summary.primary_metric_value)
        bucket["total_usage"] = round(bucket["total_usage"] + value, 4)
        bucket["account_count"] += 1
        if summary.quota_usage_ratio is not None:
            bucket["_ratio_sum"] += float(summary.quota_usage_ratio)
            bucket["_ratio_count"] += 1

        for family, amount in (summary.breakdown_by_model or {}).items():
            model_family_totals[family] += float(amount)

        account_rows.append(
            {
                "account_id": account.id,
                "account_identifier": account.account_identifier,
                "vendor_name": vendor_name,
                "plan_name": account.plan.plan_name if account.plan else None,
                "primary_member_id": account.primary_member_id,
                "usage": value,
                "currency": currency,
                "quota_usage_ratio": summary.quota_usage_ratio,
                "suggest_dedicated": account.suggest_dedicated,
            }
        )

    for bucket in by_vendor_currency.values():
        if bucket["_ratio_count"]:
            bucket["avg_quota_ratio"] = round(bucket["_ratio_sum"] / bucket["_ratio_count"], 2)
        del bucket["_ratio_sum"]
        del bucket["_ratio_count"]

    prev_period = previous_period(period)
    prev_summaries = list(
        session.scalars(
            select(UsageSummary)
            .join(AiAccount, UsageSummary.account_id == AiAccount.id)
            .where(AiAccount.team_id == team_id, UsageSummary.period == prev_period)
        )
    )
    prev_total = sum(float(s.primary_metric_value) for s in prev_summaries)
    curr_total = sum(float(s.primary_metric_value) for s in summaries)

    family_total = sum(model_family_totals.values()) or 0
    model_family_pct = {
        k: round(v / family_total * 100, 1) if family_total else 0
        for k, v in sorted(model_family_totals.items(), key=lambda x: -x[1])
    }

    submitted_ids = {s.account_id for s in summaries}
    missing_primary = sum(1 for a in active if not a.primary_member_id)

    return {
        "period": period,
        "account_count_active": len(active),
        "account_count_submitted": len(submitted_ids),
        "account_count_unsubmitted": len(active) - len(submitted_ids),
        "account_count_missing_primary": missing_primary,
        "account_count_suggest_dedicated": suggest_count,
        "by_vendor_currency": list(by_vendor_currency.values()),
        "model_family_pct": model_family_pct,
        "mom_total_usage_change_pct": pct_change(prev_total, curr_total),
        "accounts": account_rows,
    }
