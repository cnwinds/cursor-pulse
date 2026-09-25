from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.plan_infer import (
    cycle_end_from_period_usage,
    default_cursor_plan,
    infer_plan_from_period_usage,
)
from pulse.ingestion.sync_dispatch import sync_account_by_vendor
from pulse.integrations.coding_plan import fetch_glm_quota, fetch_minimax_quota
from pulse.integrations.coding_plan.zhipu import is_glm_team_account
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiVendor
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.audit import log_admin_action


def _glm_plan_for_level(repo: ToolCenterRepository, vendor_id: str, level: str | None):
    plans = repo.list_plans(vendor_id)
    if not plans:
        raise HTTPException(status_code=400, detail="未配置 GLM 套餐，请先初始化目录")
    slug = (level or "pro").strip().lower()
    plan = next((p for p in plans if p.slug == slug), None)
    if plan:
        return plan
    return next((p for p in plans if p.slug == "pro"), None) or plans[0]


def _minimax_default_plan(repo: ToolCenterRepository, vendor_id: str):
    plans = repo.list_plans(vendor_id)
    if not plans:
        raise HTTPException(status_code=400, detail="未配置 MiniMax 套餐，请先初始化目录")
    plan = next((p for p in plans if p.slug == "coding_plan"), None)
    return plan or plans[0]


def create_cursor_account(
    *,
    session: Session,
    repo: ToolCenterRepository,
    team_id: str,
    enc_key: str,
    user,
    vendor: AiVendor,
    body,
    validate_status,
    parse_optional_date,
    log_action=log_admin_action,
) -> dict:
    api_key = (body.api_key or "").strip()
    if not api_key.startswith("crsr_"):
        raise HTTPException(status_code=400, detail="API Key 须以 crsr_ 开头")

    plans = repo.list_plans(vendor.id)
    if not plans:
        raise HTTPException(status_code=400, detail="未配置 Cursor 套餐，请先初始化目录")
    status = validate_status(body.status)

    cursor_client = CursorApiClient()
    token = cursor_client.get_access_token(api_key)
    key_email = cursor_client.resolve_api_key_account_email(api_key)
    period_usage = cursor_client.get_current_period_usage(token, api_key=api_key)

    if body.plan_id:
        plan = next((p for p in plans if p.id == body.plan_id), None)
        if plan is None:
            raise HTTPException(status_code=400, detail="套餐不存在或不属于 Cursor")
    else:
        plan = infer_plan_from_period_usage(plans, period_usage) or default_cursor_plan(plans)
    if plan is None:
        raise HTTPException(status_code=400, detail="无法确定 Cursor 套餐")

    identifier = (body.account_identifier or "").strip() or (key_email or "")
    manual_resets = parse_optional_date(body.usage_resets_on)
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
        proxy_enabled=False,
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
    sync_account_by_vendor(
        session,
        enc_key,
        account,
        account.id,
        channel="web",
        member_id=user.member.id,
        cursor_client=cursor_client,
    )
    log_action(
        session,
        team_id=team_id,
        member_id=user.member.id,
        action="credential.bind",
        capability="accounts:write",
        detail=f"{account.id}:{cred.key_hint}",
    )
    log_action(
        session,
        team_id=team_id,
        member_id=user.member.id,
        action="account.create",
        capability="accounts:write",
        detail=account.account_identifier or account.id,
    )
    session.commit()
    return account


def create_coding_plan_account(
    *,
    session: Session,
    repo: ToolCenterRepository,
    team_id: str,
    enc_key: str,
    user,
    vendor: AiVendor,
    body,
    validate_status,
    log_action=log_admin_action,
):
    slug = vendor.slug
    api_key = (body.api_key or "").strip()
    if not api_key:
        raise HTTPException(status_code=400, detail="须填写 API Key")

    region = (body.api_region or "").strip()
    org_id = (body.glm_organization_id or "").strip()
    proj_id = (body.glm_project_id or "").strip()
    if slug == "glm":
        if org_id or proj_id:
            if not org_id or not proj_id:
                raise HTTPException(status_code=400, detail="智谱团队版须同时填写组织 ID 与项目 ID")
            region = "bigmodel"
        elif region not in ("zai", "bigmodel"):
            raise HTTPException(status_code=400, detail="GLM 须选择站点：zai（国际）或 bigmodel（国内）")
    if slug == "minimax" and region not in ("cn", "global"):
        raise HTTPException(status_code=400, detail="MiniMax 须选择区域：cn 或 global")

    identifier = (body.account_identifier or "").strip()
    if not identifier:
        raise HTTPException(status_code=400, detail="请填写账号标识（邮箱或备注名）")

    status = validate_status(body.status)

    if slug == "glm":
        quota = fetch_glm_quota(
            api_key,
            region=region,
            organization_id=org_id or None,
            project_id=proj_id or None,
        )
        plan = _glm_plan_for_level(repo, vendor.id, quota.plan_level)
    else:
        quota = fetch_minimax_quota(api_key, region=region)
        if body.plan_id:
            plan = next((p for p in repo.list_plans(vendor.id) if p.id == body.plan_id), None)
            if plan is None:
                raise HTTPException(status_code=400, detail="套餐不存在")
        else:
            plan = _minimax_default_plan(repo, vendor.id)

    account = repo.create_account(
        vendor_id=vendor.id,
        plan_id=plan.id,
        account_identifier=identifier,
        status=status,
        primary_member_id=body.primary_member_id,
        shared_note=body.shared_note,
        ownership=body.ownership,
        api_region=region,
        glm_organization_id=org_id or None,
        glm_project_id=proj_id or None,
        proxy_enabled=False,
    )

    cred_service = CredentialService(session, enc_key)
    cred = cred_service.bind_coding_plan_api_key(
        account_id=account.id,
        api_key=api_key,
        member_id=user.member.id,
    )
    sync_account_by_vendor(
        session,
        enc_key,
        account,
        account.id,
        channel="web",
        member_id=user.member.id,
    )
    team_suffix = ""
    if slug == "glm" and is_glm_team_account(organization_id=org_id, project_id=proj_id):
        team_suffix = ":team"
    log_action(
        session,
        team_id=team_id,
        member_id=user.member.id,
        action="credential.bind",
        capability="accounts:write",
        detail=f"{account.id}:{cred.key_hint}",
    )
    log_action(
        session,
        team_id=team_id,
        member_id=user.member.id,
        action="account.create",
        capability="accounts:write",
        detail=f"{slug}{team_suffix}:{account.account_identifier}",
    )
    session.commit()
    return account
