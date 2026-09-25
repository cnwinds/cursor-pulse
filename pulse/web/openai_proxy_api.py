from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.openai_proxy.pool import list_cp_admin_accounts, list_cp_pool_entries
from pulse.openai_proxy.upstream import CP_VENDORS, cp_gateway_endpoints
from pulse.proxy import key_crud
from pulse.proxy import service as proxy_service
from pulse.proxy.usage_rollup import rollup_proxy_usages
from pulse.storage.models import AiAccount, AiVendor, Member, ProxyKey, ProxyKeyUsage
from pulse.web.deps import PortalUser


class ToggleCpProxyEnabledBody(BaseModel):
    cp_proxy_enabled: bool


class CreateCpProxyKeyBody(BaseModel):
    member_id: str = Field(min_length=1)
    coding_plan_vendor: str = Field(pattern="^(glm|minimax|kimi)$")
    name: str | None = Field(default=None, max_length=128)
    window_5h_cost_usd: int | None = Field(default=None, ge=1)
    window_7d_cost_usd: int | None = Field(default=None, ge=1)


def _get_cp_proxy_key(session: Session, key_id: str) -> ProxyKey:
    key = session.get(ProxyKey, key_id)
    if key is None or key.mode != "coding_plan":
        raise HTTPException(status_code=404, detail="Coding Plan 密钥不存在")
    return key


def register_openai_proxy_admin_routes(app, get_db, require_capability, config) -> None:
    def _attach_openai_client_urls(row: dict, session: Session) -> None:
        endpoints = cp_gateway_endpoints(session=session, config=config)
        row["openai_endpoints"] = endpoints
        row["openai_base_url"] = endpoints[0]["openai_base_url"] if endpoints else ""

    @app.get(
        "/api/v2/openai-proxy/endpoints",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_cp_openai_endpoints(session: Session = Depends(get_db)):
        return {"endpoints": cp_gateway_endpoints(session=session, config=config)}

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
        user_name = (body.name or "").strip()
        enc = (config.credentials.encryption_key or "").strip()
        key, plaintext = key_crud.create_coding_plan_key(
            session,
            name=user_name,
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
        _attach_openai_client_urls(row, session)
        row["usage_hint"] = "OpenAI SDK: base_url + api_key=pkcp_…；走 Coding Plan 账号池转发"
        return row

    @app.get(
        "/api/v2/openai-proxy/keys",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_cp_proxy_keys(session: Session = Depends(get_db)):
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
            _attach_openai_client_urls(row, session)
            rows.append(row)
        return rows

    @app.get(
        "/api/v2/openai-proxy/keys/{key_id}/usages",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def cp_proxy_key_usages(
        key_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_db),
    ):
        key = _get_cp_proxy_key(session, key_id)
        rows = (
            session.execute(
                select(ProxyKeyUsage).where(ProxyKeyUsage.proxy_key_id == key_id).order_by(ProxyKeyUsage.ts.desc())
            )
            .scalars()
            .all()
        )
        rollup = rollup_proxy_usages(session, rows, limit=limit)
        summary = proxy_service.key_summary(session, key)
        rollup["summary"] = {
            "name": key.name,
            "key_hint": key.key_hint,
            "coding_plan_vendor": key.coding_plan_vendor,
            "request_count": summary.get("request_count", 0),
            "total_tokens": summary.get("total_tokens", 0),
            "window_5h_tokens": summary.get("window_5h_tokens", 0),
            "window_7d_tokens": summary.get("window_7d_tokens", 0),
        }
        return rollup

    @app.get("/api/v2/openai-proxy/keys/{key_id}/reveal")
    def reveal_cp_proxy_key(
        key_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("proxy:read")),
    ):
        from pulse.web.proxy_keys_api import _can_reveal_key

        key = _get_cp_proxy_key(session, key_id)
        if not _can_reveal_key(user, key):
            raise HTTPException(status_code=403, detail="无权查看该 Key")
        enc = (config.credentials.encryption_key or "").strip()
        plaintext = proxy_service.reveal_plaintext(key, enc)
        if plaintext is None:
            raise HTTPException(
                status_code=410,
                detail="该 Key 不可还原（签发时未加密保存），请重新签发",
            )
        payload: dict = {"plaintext_key": plaintext}
        _attach_openai_client_urls(payload, session)
        return payload

    @app.post(
        "/api/v2/openai-proxy/keys/{key_id}/revoke",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def revoke_cp_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_cp_proxy_key(session, key_id)
        if key.status == "revoked":
            return proxy_service.key_summary(session, key)
        key.status = "revoked"
        key.updated_at = proxy_service.utcnow()
        proxy_service.record_event(session, event_type="revoked", proxy_key_id=key.id)
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.post(
        "/api/v2/openai-proxy/keys/{key_id}/resume",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def resume_cp_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_cp_proxy_key(session, key_id)
        if not proxy_service.resume_key(session, key):
            raise HTTPException(status_code=409, detail="该 key 非 suspended 状态，无法恢复")
        session.commit()
        return proxy_service.key_summary(session, key)
