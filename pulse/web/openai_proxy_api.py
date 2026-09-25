from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from pulse.openai_proxy.pool import list_cp_admin_accounts, list_cp_pool_entries
from pulse.openai_proxy.upstream import CP_VENDORS, coding_plan_gateway_public_base
from pulse.proxy import key_crud
from pulse.proxy import service as proxy_service
from pulse.storage.models import AiAccount, AiVendor, Member
from pulse.web.deps import PortalUser


class ToggleCpProxyEnabledBody(BaseModel):
    cp_proxy_enabled: bool


class CreateCpProxyKeyBody(BaseModel):
    member_id: str = Field(min_length=1)
    coding_plan_vendor: str = Field(pattern="^(glm|minimax|kimi)$")
    name: str | None = Field(default=None, max_length=128)
    window_5h_cost_usd: int | None = Field(default=None, ge=1)
    window_7d_cost_usd: int | None = Field(default=None, ge=1)


def register_openai_proxy_admin_routes(app, get_db, require_capability, config) -> None:
    @app.get(
        "/api/v2/openai-proxy/pool",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_cp_pool(
        vendor: str = Query(..., pattern="^(glm|minimax|kimi)$"),
        session: Session = Depends(get_db),
    ):
        enc = (config.credentials.encryption_key or "").strip()
        return list_cp_pool_entries(session, vendor_slug=vendor, encryption_key=enc, include_api_keys=False)

    @app.get(
        "/api/v2/openai-proxy/accounts",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_cp_accounts(
        vendor: str = Query(..., pattern="^(glm|minimax|kimi)$"),
        session: Session = Depends(get_db),
    ):
        return list_cp_admin_accounts(session, vendor_slug=vendor)

    @app.post(
        "/api/v2/openai-proxy/accounts/{account_id}",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def toggle_cp_pool_account(
        account_id: str,
        body: ToggleCpProxyEnabledBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("proxy:write")),
    ):
        del user
        account = session.get(AiAccount, account_id)
        if account is None or account.deleted_at is not None:
            raise HTTPException(status_code=404, detail="account 不存在")
        vendor = session.get(AiVendor, account.vendor_id)
        if vendor is None or vendor.slug not in CP_VENDORS:
            raise HTTPException(status_code=400, detail="仅 Coding Plan 账号可加入 OpenAI 网关池")
        if body.cp_proxy_enabled and account.proxy_enabled:
            raise HTTPException(status_code=400, detail="Cursor MITM 入池账号不能同时加入 Coding Plan 网关池")
        if body.cp_proxy_enabled:
            from sqlalchemy import func, select

            from pulse.storage.models import AiAccountCredential

            count = session.scalar(
                select(func.count())
                .select_from(AiAccountCredential)
                .where(
                    AiAccountCredential.account_id == account.id,
                    AiAccountCredential.status == "active",
                    AiAccountCredential.key_role == "primary",
                )
            )
            from pulse.web.proxy_keys_api import _pool_account_readiness

            ready, reason = _pool_account_readiness(int(count or 0))
            if not ready:
                raise HTTPException(status_code=400, detail=reason or "无法入池")
        account.cp_proxy_enabled = body.cp_proxy_enabled
        session.commit()
        return {"id": account.id, "cp_proxy_enabled": account.cp_proxy_enabled}

    @app.post(
        "/api/v2/openai-proxy/keys",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def create_cp_proxy_key(body: CreateCpProxyKeyBody, session: Session = Depends(get_db)):
        member = session.get(Member, body.member_id)
        if member is None:
            raise HTTPException(status_code=400, detail="归属成员不存在")
        name = (body.name or "").strip() or f"{body.coding_plan_vendor.upper()} OpenAI"
        enc = (config.credentials.encryption_key or "").strip()
        key, plaintext = key_crud.create_coding_plan_key(
            session,
            name=name,
            member_id=member.id,
            coding_plan_vendor=body.coding_plan_vendor,
            window_5h_cost_limit_cents=proxy_service.usd_to_cents(body.window_5h_cost_usd),
            window_7d_cost_limit_cents=proxy_service.usd_to_cents(body.window_7d_cost_usd),
            encryption_key=enc,
        )
        session.commit()
        row = proxy_service.key_summary(session, key)
        row["plaintext_key"] = plaintext
        row["member_name"] = member.display_name
        row["openai_base_url"] = coding_plan_gateway_public_base(proxy_public_url=config.proxy.public_url)
        row["usage_hint"] = "OpenAI SDK: base_url + api_key=pkcp_…；走 Coding Plan 账号池转发"
        return row

    @app.get(
        "/api/v2/openai-proxy/keys",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_cp_proxy_keys(session: Session = Depends(get_db)):
        from sqlalchemy import select

        from pulse.storage.models import ProxyKey

        keys = (
            session.execute(select(ProxyKey).where(ProxyKey.mode == "coding_plan").order_by(ProxyKey.created_at.desc()))
            .scalars()
            .all()
        )
        member_names = {
            m.id: m.display_name
            for m in session.execute(select(Member).where(Member.id.in_({k.member_id for k in keys} or {""}))).scalars()
        }
        rows = []
        for row, key in zip(proxy_service.key_summaries(session, list(keys)), keys, strict=True):
            row["member_name"] = member_names.get(key.member_id)
            row["recoverable"] = bool(key.encrypted_key)
            row["openai_base_url"] = coding_plan_gateway_public_base(proxy_public_url=config.proxy.public_url)
            rows.append(row)
        return rows
