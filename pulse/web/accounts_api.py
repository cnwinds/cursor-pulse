from __future__ import annotations

from datetime import date

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import AccountEmailMismatchError
from pulse.web.account_create_handlers import create_coding_plan_account, create_cursor_account
from pulse.storage.models import AiAccountCredential, AiVendor, UsageDailyAggregate
from pulse.tool_center.repository import ToolCenterRepository
from pulse.util.datetime_fmt import serialize_datetime
from pulse.web.audit import log_admin_action
from pulse.web.credentials_api import (
    can_manage_credential,
    credential_status_summary,
    unbound_credential_status,
)
from pulse.web.deps import PortalUser

_ACCOUNT_STATUS_VALUES = frozenset({"trial", "shared", "dedicated", "suspended"})


class AccountCreateBody(BaseModel):
    vendor_id: str | None = None
    plan_id: str | None = None
    account_identifier: str = ""
    status: str = "shared"
    primary_member_id: str | None = None
    shared_note: str | None = None
    ownership: str = "company"
    usage_resets_on: str | None = None
    api_key: str | None = None
    api_region: str | None = None
    glm_organization_id: str | None = None
    glm_project_id: str | None = None


class AccountPatchBody(BaseModel):
    account_identifier: str | None = None
    plan_id: str | None = None
    previous_plan_id: str | None = None
    plan_effective_from: str | None = None
    plan_change_note: str | None = None
    status: str | None = None
    primary_member_id: str | None = Field(default=None)
    shared_note: str | None = None
    monthly_budget_cap: float | None = None
    budget_currency: str | None = None
    started_on: str | None = None
    renews_on: str | None = None
    usage_resets_on: str | None = None
    secondary_member_ids: list[str] | None = None


def _parse_optional_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    return date.fromisoformat(text)


def _coerce_account_date_fields(fields: dict) -> dict:
    out = dict(fields)
    for key in ("started_on", "renews_on", "usage_resets_on"):
        if key in out:
            out[key] = _parse_optional_date(out[key])
    return out


def _validate_account_status(status: str) -> str:
    value = (status or "").strip()
    if value not in _ACCOUNT_STATUS_VALUES:
        raise HTTPException(
            status_code=400,
            detail=f"账号类型无效：须为 {', '.join(sorted(_ACCOUNT_STATUS_VALUES))}",
        )
    return value


def _account_payload(account) -> dict:
    return {
        "id": account.id,
        "vendor_id": account.vendor_id,
        "vendor_name": account.vendor.name if account.vendor else None,
        "vendor_slug": account.vendor.slug if account.vendor else None,
        "api_region": account.api_region,
        "glm_organization_id": account.glm_organization_id,
        "glm_project_id": account.glm_project_id,
        "plan_id": account.plan_id,
        "plan_name": account.plan.plan_name if account.plan else None,
        "account_identifier": account.account_identifier,
        "ownership": account.ownership,
        "status": account.status,
        "primary_member_id": account.primary_member_id,
        "shared_note": account.shared_note,
        "monthly_budget_cap": float(account.monthly_budget_cap) if account.monthly_budget_cap is not None else None,
        "budget_currency": account.budget_currency,
        "started_on": account.started_on.isoformat() if account.started_on else None,
        "renews_on": account.renews_on.isoformat() if account.renews_on else None,
        "usage_resets_on": account.usage_resets_on.isoformat() if account.usage_resets_on else None,
        "resets_on_source": account.resets_on_source,
        "suggest_dedicated": account.suggest_dedicated,
        "secondary_member_ids": [m.member_id for m in account.secondary_members],
    }


def register_accounts_v2_routes(
    app,
    get_db,
    require_capability,
    team_repo_fn,
    log_action=log_admin_action,
    config=None,
):
    @app.get("/api/v2/vendors", dependencies=[Depends(require_capability("accounts:read"))])
    def list_vendors(session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        return [
            {
                "id": v.id,
                "slug": v.slug,
                "name": v.name,
                "website": v.website,
                "is_active": v.is_active,
            }
            for v in repo.list_vendors()
        ]

    @app.get("/api/v2/plans", dependencies=[Depends(require_capability("accounts:read"))])
    def list_plans(vendor_id: str | None = None, session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        return [
            {
                "id": p.id,
                "vendor_id": p.vendor_id,
                "vendor_name": p.vendor.name if p.vendor else None,
                "plan_name": p.plan_name,
                "slug": p.slug,
                "billing_type": p.billing_type,
                "price_amount": float(p.price_amount),
                "price_currency": p.price_currency,
                "quota_ratio_enabled": p.quota_ratio_enabled,
                "quota_denominator": float(p.quota_denominator) if p.quota_denominator is not None else None,
                "upgrade_threshold_pct": p.upgrade_threshold_pct,
                "usage_submit_methods": p.usage_submit_methods or [],
            }
            for p in repo.list_plans(vendor_id)
        ]

    @app.get("/api/v2/accounts")
    def list_accounts(
        status: str | None = None,
        vendor_slug: str | None = Query(default=None),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:read")),
    ):
        """List accounts with credential summary embedded (avoids N+1 /credentials)."""
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        accounts = list(repo.list_accounts(status=status, vendor_slug=vendor_slug))
        key_vendor_slugs = frozenset({"cursor", "glm", "minimax", "kimi"})
        keyed_ids = [a.id for a in accounts if a.vendor is not None and a.vendor.slug in key_vendor_slugs]
        cred_by_account: dict[str, AiAccountCredential] = {}
        if keyed_ids:
            rows = session.scalars(
                select(AiAccountCredential).where(AiAccountCredential.account_id.in_(keyed_ids))
            ).all()
            # Prefer active over revoked if duplicates exist historically.
            for row in rows:
                existing = cred_by_account.get(row.account_id)
                if existing is None or (existing.status != "active" and row.status == "active"):
                    cred_by_account[row.account_id] = row

        payloads = []
        for account in accounts:
            payload = _account_payload(account)
            if account.vendor is None or account.vendor.slug not in key_vendor_slugs:
                payload["credential"] = None
            elif can_manage_credential(user, account):
                payload["credential"] = credential_status_summary(cred_by_account.get(account.id))
            else:
                # Same shape as unbound so UI badges stay stable without leaking
                # peer credential hints to viewers who cannot manage the row.
                payload["credential"] = unbound_credential_status()
            payloads.append(payload)
        return payloads

    @app.post("/api/v2/accounts", dependencies=[Depends(require_capability("accounts:write"))])
    def create_account(
        body: AccountCreateBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        api_key = (body.api_key or "").strip()
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="新增账号须填写 API Key",
            )
        if config is None:
            raise HTTPException(status_code=503, detail="服务未配置凭证加密")
        enc_key = (config.credentials.encryption_key or "").strip()
        if not enc_key:
            raise HTTPException(
                status_code=503,
                detail="未配置凭证加密密钥（PULSE_CREDENTIAL_ENCRYPTION_KEY）",
            )

        if body.vendor_id:
            vendor = session.get(AiVendor, body.vendor_id)
        else:
            vendor = repo.get_vendor_by_slug("cursor")
        if vendor is None:
            raise HTTPException(status_code=400, detail="厂家不存在")
        if vendor.slug not in ("cursor", "glm", "minimax", "kimi"):
            raise HTTPException(status_code=400, detail="不支持的账号厂家")
        if vendor.slug == "cursor" and not api_key.startswith("crsr_"):
            raise HTTPException(status_code=400, detail="API Key 须以 crsr_ 开头")

        try:
            if vendor.slug == "cursor":
                account = create_cursor_account(
                    session=session,
                    repo=repo,
                    team_id=team.id,
                    enc_key=enc_key,
                    user=user,
                    vendor=vendor,
                    body=body,
                    validate_status=_validate_account_status,
                    parse_optional_date=_parse_optional_date,
                    log_action=log_action,
                )
            else:
                account = create_coding_plan_account(
                    session=session,
                    repo=repo,
                    team_id=team.id,
                    enc_key=enc_key,
                    user=user,
                    vendor=vendor,
                    body=body,
                    validate_status=_validate_account_status,
                    log_action=log_action,
                )
        except HTTPException:
            session.rollback()
            raise
        except AccountEmailMismatchError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "account_email_mismatch",
                    "message": str(exc),
                    "ledger_email": exc.ledger_email,
                    "key_email": exc.key_email,
                },
            ) from exc
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        account = repo.get_account(account.id)
        return _account_payload(account)

    @app.patch("/api/v2/accounts/{account_id}", dependencies=[Depends(require_capability("accounts:write"))])
    def patch_account(
        account_id: str,
        body: AccountPatchBody,
        session: Session = Depends(get_db),
        user=Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        account = repo.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        if body.status is not None:
            _validate_account_status(body.status)
        fields = _coerce_account_date_fields(
            body.model_dump(
                exclude_unset=True,
                exclude={
                    "secondary_member_ids",
                    "plan_effective_from",
                    "plan_change_note",
                    "previous_plan_id",
                },
            )
        )
        plan_effective_from = _parse_optional_date(body.plan_effective_from)
        if body.previous_plan_id and plan_effective_from:
            repo.backfill_plan_upgrade(
                account_id,
                previous_plan_id=body.previous_plan_id,
                effective_from=plan_effective_from,
                changed_by_member_id=user.member.id,
                note=body.plan_change_note,
            )
        new_plan_id = fields.pop("plan_id", None)
        if new_plan_id and new_plan_id != account.plan_id:
            eff = plan_effective_from or date.today()
            repo.change_account_plan(
                account_id,
                new_plan_id=new_plan_id,
                effective_from=eff,
                changed_by_member_id=user.member.id,
                note=body.plan_change_note,
            )
            fields.pop("plan_id", None)
        if fields:
            if "account_identifier" in fields and fields["account_identifier"] is not None:
                fields["account_identifier"] = fields["account_identifier"].strip()
            if "usage_resets_on" in fields and fields["usage_resets_on"] is not None:
                fields["resets_on_source"] = "manual-locked"
            repo.update_account(account_id, **fields)
        if body.secondary_member_ids is not None:
            repo.set_secondary_members(account_id, body.secondary_member_ids)
        session.commit()
        log_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="account.update",
            capability="accounts:write",
            detail=account_id,
        )
        session.commit()
        account = repo.get_account(account_id)
        return _account_payload(account)

    @app.delete(
        "/api/v2/accounts/{account_id}",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def delete_account(
        account_id: str,
        session: Session = Depends(get_db),
        user=Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        account = repo.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        try:
            mode = repo.delete_account(account_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        log_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="account.delete",
            capability="accounts:write",
            detail=f"{account_id}:{mode}",
        )
        session.commit()
        return {"ok": True, "account_id": account_id, "mode": mode}

    @app.post(
        "/api/v2/accounts/{account_id}/recompute-summary",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def recompute_summary(
        account_id: str,
        period: str,
        session: Session = Depends(get_db),
        user=Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        account = repo.get_account(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="账号不存在")
        row = repo.recompute_usage_summary(account_id, period)
        if not row:
            raise HTTPException(status_code=404, detail="该账期无已提交用量")
        session.commit()
        log_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="account.recompute_summary",
            capability="accounts:write",
            detail=f"{account_id}:{period}",
        )
        session.commit()
        return {
            "account_id": account_id,
            "period": period,
            "quota_usage_ratio": row.quota_usage_ratio,
            "cycle_quota_usage_ratio": row.cycle_quota_usage_ratio,
            "cycle_metric_value": float(row.cycle_metric_value) if row.cycle_metric_value is not None else None,
        }

    @app.get(
        "/api/v2/usage-summaries",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_usage_summaries(period: str, session: Session = Depends(get_db)):
        from sqlalchemy import select

        from pulse.storage.models import AiAccount, UsageSummary

        team, _ = team_repo_fn(session)
        rows = session.scalars(
            select(UsageSummary)
            .join(AiAccount, UsageSummary.account_id == AiAccount.id)
            .where(AiAccount.team_id == team.id, UsageSummary.period == period, AiAccount.deleted_at.is_(None))
        ).all()
        return [
            {
                "id": r.id,
                "account_id": r.account_id,
                "period": r.period,
                "primary_metric_value": float(r.primary_metric_value),
                "primary_metric_unit": r.primary_metric_unit,
                "reported_spend_usd": float(r.reported_spend_usd) if r.reported_spend_usd is not None else None,
                "estimated_included_spend_usd": float(r.estimated_included_spend_usd)
                if r.estimated_included_spend_usd is not None
                else None,
                "quota_usage_ratio": r.quota_usage_ratio,
                "billing_cycle_start": r.billing_cycle_start.isoformat() if r.billing_cycle_start else None,
                "billing_cycle_end": r.billing_cycle_end.isoformat() if r.billing_cycle_end else None,
                "plan_id_used": r.plan_id_used,
                "quota_denominator_snapshot": float(r.quota_denominator_snapshot)
                if r.quota_denominator_snapshot is not None
                else None,
                "cycle_metric_value": float(r.cycle_metric_value) if r.cycle_metric_value is not None else None,
                "cycle_quota_usage_ratio": r.cycle_quota_usage_ratio,
                "estimation_coverage_pct": r.estimation_coverage_pct,
                "unmatched_models": r.unmatched_models or [],
                "cursor_pools": r.cursor_pools,
                "external_models": r.external_models,
                "shared_note": r.shared_note,
                "breakdown_by_model": r.breakdown_by_model,
            }
            for r in rows
        ]

    @app.get(
        "/api/v2/accounts/{account_id}/usage/daily",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_account_daily_usage(
        account_id: str,
        start: date = Query(..., description="起始日期 YYYY-MM-DD"),
        end: date = Query(..., description="结束日期 YYYY-MM-DD"),
        session: Session = Depends(get_db),
    ):
        """Per-account daily model aggregates for quota-board detail dialog.

        Rows are newest ``event_date`` first so the UI can show recent days at the top.
        """
        if end < start:
            raise HTTPException(status_code=400, detail="end 不能早于 start")
        team, _ = team_repo_fn(session)
        tool_repo = ToolCenterRepository(session, team.id)
        account = tool_repo.get_account(account_id)
        if not account or account.team_id != team.id:
            raise HTTPException(status_code=404, detail="账号不存在")

        rows = session.scalars(
            select(UsageDailyAggregate)
            .where(
                UsageDailyAggregate.account_id == account_id,
                UsageDailyAggregate.event_date >= start,
                UsageDailyAggregate.event_date <= end,
            )
            .order_by(UsageDailyAggregate.event_date.desc(), UsageDailyAggregate.model)
        ).all()
        return [
            {
                "account_id": row.account_id,
                "event_date": row.event_date.isoformat(),
                "model": row.model,
                "kind_family": getattr(row, "kind_family", None) or "unknown",
                "event_count": row.event_count,
                "total_cost_usd": float(row.total_cost_usd),
                "tokens_input": row.tokens_input,
                "tokens_output": row.tokens_output,
                "tokens_cache_read": row.tokens_cache_read,
                "updated_at": serialize_datetime(row.updated_at),
            }
            for row in rows
        ]

    @app.get(
        "/api/v2/reports/{period}",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def get_v2_report(period: str, session: Session = Depends(get_db)):
        from pulse.tool_center.aggregate import aggregate_account_metrics
        from pulse.tool_center.briefing import (
            build_anonymous_group_digest,
            build_manager_briefing,
        )

        team, _ = team_repo_fn(session)
        metrics = aggregate_account_metrics(session, period, team_id=team.id)
        return {
            "metrics": metrics,
            "manager_briefing": build_manager_briefing(session, period, team_id=team.id),
            "group_digest": build_anonymous_group_digest(session, period, team_id=team.id),
        }

    @app.get(
        "/api/v2/members",
        dependencies=[Depends(require_capability("accounts:read"))],
    )
    def list_members_for_accounts(session: Session = Depends(get_db)):
        from sqlalchemy import select

        from pulse.storage.models import Member

        team, _ = team_repo_fn(session)
        members = session.scalars(
            select(Member).where(
                Member.team_id == team.id,
                Member.status == "active",
            )
        ).all()
        return [
            {
                "id": m.id,
                "display_name": m.display_name,
                "channel_user_id": m.channel_user_id,
                "department_name": m.department_name,
            }
            for m in members
        ]
