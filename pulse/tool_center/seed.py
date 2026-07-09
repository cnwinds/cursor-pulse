from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import AiAccount, AiPlan, AiVendor, Team


def seed_v2_catalog(session: Session, team: Team) -> dict[str, int]:
    """预置厂家、套餐与 3 个 Cursor Pro+ 试用账号。幂等：按 slug 跳过已存在记录。"""
    counts = {"vendors": 0, "plans": 0, "accounts": 0}

    vendors_spec = [
        ("cursor", "Cursor", "https://cursor.com/docs/models-and-pricing"),
        ("zhipu", "智谱", "https://open.bigmodel.cn"),
        ("minimax", "MiniMax", "https://platform.minimax.io/docs/pricing/overview"),
        ("codex", "Codex (OpenAI)", "https://developers.openai.com/codex/pricing"),
    ]
    vendor_ids: dict[str, str] = {}
    for slug, name, website in vendors_spec:
        existing = session.scalar(select(AiVendor).where(AiVendor.slug == slug))
        if existing:
            vendor_ids[slug] = existing.id
            continue
        row = AiVendor(slug=slug, name=name, website=website, is_active=True)
        session.add(row)
        session.flush()
        vendor_ids[slug] = row.id
        counts["vendors"] += 1

    effective = date(2026, 1, 1)
    plans_spec = [
        (
            "cursor",
            "pro",
            "Pro",
            "fixed_monthly_pool",
            20,
            "USD",
            {"spend_cap_usd": 20},
            True,
            20.0,
            ["api_key"],
        ),
        (
            "cursor",
            "pro_plus",
            "Pro+",
            "fixed_monthly_pool",
            60,
            "USD",
            {"spend_cap_usd": 70},
            True,
            70.0,
            ["api_key"],
        ),
        (
            "cursor",
            "ultra",
            "Ultra",
            "fixed_monthly_pool",
            200,
            "USD",
            {"spend_cap_usd": 400},
            True,
            400.0,
            ["api_key"],
        ),
        (
            "zhipu",
            "glm_coding_lite",
            "GLM Coding Lite",
            "subscription_quota",
            49,
            "CNY",
            {"mcp_calls_per_month": 100},
            False,
            None,
            ["screenshot", "manual"],
        ),
        (
            "minimax",
            "token_plus",
            "Token Plan Plus",
            "subscription_quota",
            20,
            "USD",
            {"estimated_calls_per_month": 34000},
            False,
            None,
            ["screenshot", "manual"],
        ),
        (
            "codex",
            "chatgpt_plus",
            "ChatGPT Plus (Codex)",
            "rolling_window",
            20,
            "USD",
            {"local_messages_per_5h": {"min": 15, "max": 80}},
            False,
            None,
            ["screenshot", "manual"],
        ),
    ]

    plan_ids: dict[str, str] = {}
    for (
        vendor_slug,
        plan_slug,
        plan_name,
        billing_type,
        price,
        currency,
        quota,
        ratio_enabled,
        denominator,
        submit_methods,
    ) in plans_spec:
        key = f"{vendor_slug}:{plan_slug}"
        vendor_id = vendor_ids[vendor_slug]
        existing = session.scalar(
            select(AiPlan).where(
                AiPlan.vendor_id == vendor_id,
                AiPlan.slug == plan_slug,
                AiPlan.effective_from == effective,
            )
        )
        if existing:
            plan_ids[key] = existing.id
            continue
        row = AiPlan(
            vendor_id=vendor_id,
            plan_name=plan_name,
            slug=plan_slug,
            billing_type=billing_type,
            price_amount=price,
            price_currency=currency,
            included_quota=quota,
            quota_ratio_enabled=ratio_enabled,
            quota_denominator=denominator,
            upgrade_threshold_pct=95.0,
            upgrade_consecutive_months=2,
            usage_submit_methods=submit_methods,
            effective_from=effective,
        )
        session.add(row)
        session.flush()
        plan_ids[key] = row.id
        counts["plans"] += 1

    pro_plus_id = plan_ids["cursor:pro_plus"]
    cursor_vendor_id = vendor_ids["cursor"]
    for idx in range(1, 4):
        identifier = f"cursor-shared-{idx:02d}@company.com"
        existing = session.scalar(
            select(AiAccount).where(
                AiAccount.team_id == team.id,
                AiAccount.account_identifier == identifier,
            )
        )
        if existing:
            continue
        session.add(
            AiAccount(
                team_id=team.id,
                vendor_id=cursor_vendor_id,
                plan_id=pro_plus_id,
                account_identifier=identifier,
                ownership="company",
                status="trial",
                shared_note="试用池共享账号，请指定主使用人后提交用量",
            )
        )
        counts["accounts"] += 1

    session.flush()
    return counts
