from __future__ import annotations

from datetime import datetime

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy import service as proxy_service
from pulse.proxy.usage_rollup import rollup_proxy_usages
from pulse.util.datetime_fmt import serialize_datetime
from pulse.storage.models import (
    AiAccount,
    AiAccountCredential,
    AiPlan,
    AiVendor,
    Member,
    ProxyKey,
    ProxyKeyUsage,
)
from pulse.web.deps import PortalUser
from pulse.web.permissions import has_permission


class CreateProxyKeyBody(BaseModel):
    member_id: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=128)
    window_5h_cost_usd: int | None = Field(default=None, ge=1)
    window_7d_cost_usd: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    # Accepted but ignored (compat): always quota; empty windows = unlimited.
    mode: str | None = None


class UpdateProxyKeyBody(BaseModel):
    name: str | None = None
    window_5h_cost_usd: int | None = Field(default=None, ge=1)
    window_7d_cost_usd: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


class ToggleProxyEnabledBody(BaseModel):
    proxy_enabled: bool


def _active_primary_counts(
    creds: list[AiAccountCredential], account_ids: list[str]
) -> dict[str, int]:
    counts = {aid: 0 for aid in account_ids}
    for cred in creds:
        if cred.status == "active" and cred.key_role == "primary":
            counts[cred.account_id] = counts.get(cred.account_id, 0) + 1
    return counts


def _pool_account_readiness(active_count: int) -> tuple[bool, str | None]:
    if active_count == 0:
        return False, "无可用主 Key"
    if active_count > 1:
        return False, "存在多个主 Key，请只保留一个"
    return True, None


def _require_pool_ready(session: Session, account_id: str) -> None:
    from sqlalchemy import func

    count = session.scalar(
        select(func.count())
        .select_from(AiAccountCredential)
        .where(
            AiAccountCredential.account_id == account_id,
            AiAccountCredential.status == "active",
            AiAccountCredential.key_role == "primary",
        )
    )
    ready, reason = _pool_account_readiness(int(count or 0))
    if not ready:
        raise HTTPException(status_code=400, detail=reason or "无法入池")


# 兼容旧客户端
ToggleCredentialBody = ToggleProxyEnabledBody


def _get_key(session: Session, key_id: str) -> ProxyKey:
    key = session.get(ProxyKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="接入密钥不存在")
    return key


def _can_reveal_key(user: PortalUser, key: ProxyKey) -> bool:
    if has_permission(user.member, "proxy:write"):
        return True
    return has_permission(user.member, "proxy:read") and key.member_id == user.member.id


def register_proxy_keys_routes(app, get_db, require_capability, config) -> None:
    @app.get(
        "/api/v2/proxy-keys",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_proxy_keys(session: Session = Depends(get_db)):
        keys = (
            session.execute(select(ProxyKey).order_by(ProxyKey.created_at.desc()))
            .scalars()
            .all()
        )
        member_names = {
            m.id: m.display_name
            for m in session.execute(
                select(Member).where(Member.id.in_({k.member_id for k in keys} or {""}))
            ).scalars()
        }
        rows = []
        for key in keys:
            row = proxy_service.key_summary(session, key)
            row["member_name"] = member_names.get(key.member_id)
            row["recoverable"] = bool(key.encrypted_key)
            rows.append(row)
        return rows

    @app.post("/api/v2/proxy-keys")
    def create_proxy_key(
        body: CreateProxyKeyBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("proxy:write")),
    ):
        member = session.get(Member, body.member_id)
        if member is None:
            raise HTTPException(status_code=400, detail="归属成员不存在")
        name = (body.name or "").strip() or member.display_name
        enc = (config.credentials.encryption_key or "").strip()
        key, plaintext = proxy_service.create_key(
            session,
            name=name,
            member_id=member.id,
            window_5h_cost_limit_cents=proxy_service.usd_to_cents(body.window_5h_cost_usd),
            window_7d_cost_limit_cents=proxy_service.usd_to_cents(body.window_7d_cost_usd),
            expires_at=body.expires_at,
            encryption_key=enc,
        )
        session.commit()
        row = proxy_service.key_summary(session, key)
        row["plaintext_key"] = plaintext  # 仅此一次随创建响应
        row["member_name"] = member.display_name
        row["recoverable"] = bool(key.encrypted_key)
        row["proxy_url"] = (config.proxy.public_url or "http://127.0.0.1:8317").rstrip("/")
        return row

    @app.get("/api/v2/proxy-keys/{key_id}/client-setup")
    def client_setup(
        key_id: str,
        shell: str = Query(default="powershell", pattern="^(bash|powershell)$"),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("proxy:read")),
    ):
        key = _get_key(session, key_id)
        if not _can_reveal_key(user, key):
            raise HTTPException(status_code=403, detail="无权查看该 Key")
        enc = (config.credentials.encryption_key or "").strip()
        plaintext = proxy_service.reveal_plaintext(key, enc)
        if plaintext is None:
            raise HTTPException(
                status_code=410,
                detail="该 Key 不可还原（历史 Key 未加密保存），请新建",
            )
        proxy_url = (config.proxy.public_url or "http://127.0.0.1:8317").rstrip("/")
        command = proxy_service.build_client_command(
            shell=shell, proxy_url=proxy_url, plaintext_key=plaintext
        )
        return {
            "plaintext_key": plaintext,
            "proxy_url": proxy_url,
            "shell": shell,
            "command": command,
        }
    @app.patch(
        "/api/v2/proxy-keys/{key_id}",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def update_proxy_key(
        key_id: str, body: UpdateProxyKeyBody, session: Session = Depends(get_db)
    ):
        key = _get_key(session, key_id)
        if key.status == "revoked":
            raise HTTPException(status_code=409, detail="已吊销的 key 不可编辑")
        data = body.model_dump(exclude_unset=True)
        if "name" in data:
            if data["name"] is not None:
                key.name = data["name"]
        if "window_5h_cost_usd" in data:
            key.window_5h_cost_limit_cents = proxy_service.usd_to_cents(
                data["window_5h_cost_usd"]
            )
        if "window_7d_cost_usd" in data:
            key.window_7d_cost_limit_cents = proxy_service.usd_to_cents(
                data["window_7d_cost_usd"]
            )
        if "expires_at" in data:
            key.expires_at = data["expires_at"]
        key.updated_at = proxy_service.utcnow()
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.post(
        "/api/v2/proxy-keys/{key_id}/revoke",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def revoke_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_key(session, key_id)
        key.status = "revoked"
        key.updated_at = proxy_service.utcnow()
        proxy_service.record_event(session, event_type="revoked", proxy_key_id=key.id)
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.post(
        "/api/v2/proxy-keys/{key_id}/resume",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def resume_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_key(session, key_id)
        if not proxy_service.resume_key(session, key):
            raise HTTPException(status_code=409, detail="该 key 非 suspended 状态，无法恢复")
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.get(
        "/api/v2/proxy-keys/{key_id}/usages",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_proxy_key_usages(
        key_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        session: Session = Depends(get_db),
    ):
        _get_key(session, key_id)
        all_rows = (
            session.execute(
                select(ProxyKeyUsage)
                .where(ProxyKeyUsage.proxy_key_id == key_id)
                .order_by(ProxyKeyUsage.ts.desc())
            )
            .scalars()
            .all()
        )
        return rollup_proxy_usages(session, all_rows, limit=limit)

    @app.get(
        "/api/v2/proxy-pool/accounts",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_pool_accounts(session: Session = Depends(get_db)):
        accounts = (
            session.execute(
                select(AiAccount)
                .join(AiVendor, AiAccount.vendor_id == AiVendor.id)
                .where(
                    AiVendor.slug == "cursor",
                    AiAccount.deleted_at.is_(None),
                )
                .order_by(AiAccount.account_identifier)
            )
            .scalars()
            .all()
        )
        if not accounts:
            return []
        plan_ids = {a.plan_id for a in accounts}
        member_ids = {a.primary_member_id for a in accounts if a.primary_member_id}
        plans = {
            p.id: p.plan_name
            for p in session.execute(select(AiPlan).where(AiPlan.id.in_(plan_ids))).scalars()
        }
        members = {
            m.id: m.display_name
            for m in session.execute(
                select(Member).where(Member.id.in_(member_ids or {""}))
            ).scalars()
        }
        account_ids = [a.id for a in accounts]
        creds = (
            session.execute(
                select(AiAccountCredential).where(
                    AiAccountCredential.account_id.in_(account_ids)
                )
            )
            .scalars()
            .all()
        )
        active_counts = _active_primary_counts(creds, account_ids)
        out = []
        for a in accounts:
            active_count = active_counts.get(a.id, 0)
            ready, ready_reason = _pool_account_readiness(active_count)
            proxy_enabled = bool(a.proxy_enabled)
            out.append(
                {
                    "id": a.id,
                    "account_identifier": a.account_identifier,
                    "primary_member_name": members.get(a.primary_member_id)
                    if a.primary_member_id
                    else None,
                    "proxy_enabled": proxy_enabled,
                    "pool_ready": ready,
                    "pool_ready_reason": ready_reason,
                    "pool_effective": proxy_enabled and ready,
                    # deprecated: 保留兼容旧客户端
                    "plan_name": plans.get(a.plan_id),
                    "status": a.status,
                    "active_credential_count": active_count,
                }
            )
        return out

    @app.get(
        "/api/v2/proxy-pool/ranking",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def pool_ranking(session: Session = Depends(get_db)):
        """当前代理池打分表：入选排序 + 硬过滤排除项。"""
        return proxy_service.list_pool_ranking_board(
            session,
            loan_selection=config.tool_center.loan_selection,
        )

    @app.post(
        "/api/v2/proxy-pool/accounts/{account_id}",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def toggle_pool_account(
        account_id: str, body: ToggleProxyEnabledBody, session: Session = Depends(get_db)
    ):
        account = session.get(AiAccount, account_id)
        if account is None or account.deleted_at is not None:
            raise HTTPException(status_code=404, detail="account 不存在")
        vendor = session.get(AiVendor, account.vendor_id)
        if vendor is None or vendor.slug != "cursor":
            raise HTTPException(status_code=404, detail="account 不存在")
        if body.proxy_enabled:
            _require_pool_ready(session, account.id)
        account.proxy_enabled = body.proxy_enabled
        account.updated_at = proxy_service.utcnow()
        proxy_service.record_event(
            session,
            event_type="pool_toggled",
            detail=f"account_id={account.id} proxy_enabled={account.proxy_enabled}",
        )
        session.commit()
        return {"id": account.id, "proxy_enabled": account.proxy_enabled}

    @app.get(
        "/api/v2/proxy-pool/credentials",
        dependencies=[Depends(require_capability("proxy:read"))],
    )
    def list_pool_credentials(session: Session = Depends(get_db)):
        """Deprecated: 请改用 /api/v2/proxy-pool/accounts。"""
        rows = (
            session.execute(
                select(AiAccountCredential)
                .join(AiVendor, AiAccountCredential.vendor_id == AiVendor.id)
                .join(AiAccount, AiAccountCredential.account_id == AiAccount.id)
                .where(AiVendor.slug == "cursor", AiAccount.deleted_at.is_(None))
                .order_by(AiAccountCredential.bound_at.desc())
            )
            .scalars()
            .all()
        )
        accounts = {
            a.id: a
            for a in session.execute(
                select(AiAccount).where(
                    AiAccount.id.in_({c.account_id for c in rows} or {""})
                )
            ).scalars()
        }
        return [
            {
                "id": c.id,
                "account_id": c.account_id,
                "key_hint": c.key_hint,
                "display_name": c.display_name,
                "status": c.status,
                # 对外语义改为账号级入池
                "proxy_enabled": bool(accounts[c.account_id].proxy_enabled)
                if c.account_id in accounts
                else False,
            }
            for c in rows
        ]

    @app.post(
        "/api/v2/proxy-pool/credentials/{cred_id}",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def toggle_pool_credential(
        cred_id: str, body: ToggleProxyEnabledBody, session: Session = Depends(get_db)
    ):
        """Deprecated: 请改用 /api/v2/proxy-pool/accounts/{account_id}。改为切换所属账号。"""
        cred = session.get(AiAccountCredential, cred_id)
        if cred is None:
            raise HTTPException(status_code=404, detail="credential 不存在")
        vendor = session.get(AiVendor, cred.vendor_id)
        if vendor is None or vendor.slug != "cursor":
            raise HTTPException(status_code=404, detail="credential 不存在")
        account = session.get(AiAccount, cred.account_id)
        if account is None or account.deleted_at is not None:
            raise HTTPException(status_code=404, detail="credential 不存在")
        if body.proxy_enabled:
            _require_pool_ready(session, account.id)
        account.proxy_enabled = body.proxy_enabled
        account.updated_at = proxy_service.utcnow()
        # 同步写凭证列，便于旧数据观察；池过滤已不读此列
        cred.proxy_enabled = body.proxy_enabled
        proxy_service.record_event(
            session,
            event_type="pool_toggled",
            credential_id=cred.id,
            detail=f"account_id={account.id} proxy_enabled={account.proxy_enabled}",
        )
        session.commit()
        return {"id": cred.id, "proxy_enabled": account.proxy_enabled}
