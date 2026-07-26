from __future__ import annotations

from datetime import date

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import AccountEmailMismatchError, CredentialService
from pulse.ingestion.plan_infer import (
    cycle_end_from_period_usage,
    default_cursor_plan,
    infer_plan_from_period_usage,
)
from pulse.ingestion.sync import CursorSyncService
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiVendor
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.audit import log_admin_action

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
        "plan_id": account.plan_id,
        "plan_name": account.plan.plan_name if account.plan else None,
        "account_identifier": account.account_identifier,
        "ownership": account.ownership,
        "status": account.status,
        "primary_member_id": account.primary_member_id,
        "shared_note": account.shared_note,
        "monthly_budget_cap": float(account.monthly_budget_cap)
        if account.monthly_budget_cap is not None
        else None,
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
                "quota_denominator": float(p.quota_denominator)
                if p.quota_denominator is not None
                else None,
                "upgrade_threshold_pct": p.upgrade_threshold_pct,
                "usage_submit_methods": p.usage_submit_methods or [],
            }
            for p in repo.list_plans(vendor_id)
        ]

    @app.get("/api/v2/accounts", dependencies=[Depends(require_capability("accounts:read"))])
    def list_accounts(status: str | None = None, session: Session = Depends(get_db)):
        team, _ = team_repo_fn(session)
        repo = ToolCenterRepository(session, team.id)
        return [_account_payload(a) for a in repo.list_accounts(status=status)]

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
                detail="新增账号须填写 API Key（套餐、账号标识与用量重置将自动获取）",
            )
        if not api_key.startswith("crsr_"):
            raise HTTPException(status_code=400, detail="API Key 须以 crsr_ 开头")
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
        if vendor is None or vendor.slug != "cursor":
            raise HTTPException(status_code=400, detail="仅支持新增 Cursor 账号")

        plans = repo.list_plans(vendor.id)
        if not plans:
            raise HTTPException(status_code=400, detail="未配置 Cursor 套餐，请先初始化目录")
        status = _validate_account_status(body.status)

        try:
            cursor_client = CursorApiClient()
            token = cursor_client.get_access_token(api_key)
            key_email = cursor_client.resolve_api_key_account_email(api_key)
            period_usage = cursor_client.get_current_period_usage(token, api_key=api_key)

            if body.plan_id:
                plan = next((p for p in plans if p.id == body.plan_id), None)
                if plan is None:
                    raise HTTPException(status_code=400, detail="套餐不存在或不属于 Cursor")
            else:
                plan = infer_plan_from_period_usage(plans, period_usage) or default_cursor_plan(
                    plans
                )
            if plan is None:
                raise HTTPException(status_code=400, detail="无法确定 Cursor 套餐")

            identifier = (body.account_identifier or "").strip() or (key_email or "")
            manual_resets = _parse_optional_date(body.usage_resets_on)
            usage_resets_on = manual_resets or cycle_end_from_period_usage(period_usage)

            account = repo.create_account(
                vendor_id=vendor.id,
                plan_id=plan.id,
                account_identifier=identifier,
                status=status,
                primary_member_id=body.primary_member_id,
                shared_note=body.shared_note,
                ownership=body.ownership,
                usage_resets_on=usage_resets_on,
            )
            if manual_resets:
                account.resets_on_source = "manual-locked"
            elif usage_resets_on:
                account.resets_on_source = "api"

            cred_service = CredentialService(session, enc_key, cursor_client=cursor_client)
            cred = cred_service.bind_cursor_api_key(
                account_id=account.id,
                api_key=api_key,
                member_id=user.member.id,
            )
            CursorSyncService(session, enc_key, cursor_client=cursor_client).sync_account(
                account.id,
                channel="web",
                member_id=user.member.id,
            )
            log_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="credential.bind",
                capability="accounts:write",
                detail=f"{account.id}:{cred.key_hint}",
            )
            log_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="account.create",
                capability="accounts:write",
                detail=account.account_identifier or account.id,
            )
            session.commit()
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
            "cycle_metric_value": float(row.cycle_metric_value)
            if row.cycle_metric_value is not None
            else None,
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
                "reported_spend_usd": float(r.reported_spend_usd)
                if r.reported_spend_usd is not None
                else None,
                "estimated_included_spend_usd": float(r.estimated_included_spend_usd)
                if r.estimated_included_spend_usd is not None
                else None,
                "quota_usage_ratio": r.quota_usage_ratio,
                "billing_cycle_start": r.billing_cycle_start.isoformat()
                if r.billing_cycle_start
                else None,
                "billing_cycle_end": r.billing_cycle_end.isoformat()
                if r.billing_cycle_end
                else None,
                "plan_id_used": r.plan_id_used,
                "quota_denominator_snapshot": float(r.quota_denominator_snapshot)
                if r.quota_denominator_snapshot is not None
                else None,
                "cycle_metric_value": float(r.cycle_metric_value)
                if r.cycle_metric_value is not None
                else None,
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
