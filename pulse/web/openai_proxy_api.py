from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pulse.openai_proxy.pool import list_cp_pool_entries
from pulse.openai_proxy.upstream import CP_VENDORS
from pulse.storage.models import AiAccount, AiVendor
from pulse.web.deps import PortalUser


class ToggleCpProxyEnabledBody(BaseModel):
    cp_proxy_enabled: bool


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
        account.cp_proxy_enabled = body.cp_proxy_enabled
        session.commit()
        return {"id": account.id, "cp_proxy_enabled": account.cp_proxy_enabled}
