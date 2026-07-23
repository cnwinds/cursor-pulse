from __future__ import annotations

from datetime import datetime

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy import service as proxy_service
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
    mode: str = Field(pattern="^(unlimited|quota)$")
    member_id: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=128)
    token_limit: int | None = Field(default=None, ge=0)
    cost_limit_cents: int | None = Field(default=None, ge=0)
    window_5h_token_limit: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None


class UpdateProxyKeyBody(BaseModel):
    name: str | None = None
    token_limit: int | None = Field(default=None, ge=0)
    cost_limit_cents: int | None = Field(default=None, ge=0)
    window_5h_token_limit: int | None = Field(default=None, ge=0)
    expires_at: datetime | None = None


class ToggleProxyEnabledBody(BaseModel):
    proxy_enabled: bool


# 兼容旧客户端
ToggleCredentialBody = ToggleProxyEnabledBody


def _get_key(session: Session, key_id: str) -> ProxyKey:
    key = session.get(ProxyKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="proxy key 不存在")
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
            mode=body.mode,
            token_limit=body.token_limit,
            cost_limit_cents=body.cost_limit_cents,
            window_5h_token_limit=body.window_5h_token_limit,
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
        for field, value in body.model_dump(exclude_unset=True).items():
            if field == "name" and value is None:
                continue
            setattr(key, field, value)
        key.updated_at = proxy_service._utcnow()
        session.commit()
        return proxy_service.key_summary(session, key)

    @app.post(
        "/api/v2/proxy-keys/{key_id}/revoke",
        dependencies=[Depends(require_capability("proxy:write"))],
    )
    def revoke_proxy_key(key_id: str, session: Session = Depends(get_db)):
        key = _get_key(session, key_id)
        key.status = "revoked"
        key.updated_at = proxy_service._utcnow()
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
            raise HTTPException(status_code=409, detail="额度仍超限或该 key 非 suspended 状态")
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
        cred_ids = {u.credential_id for u in all_rows if u.credential_id}
        cred_to_account: dict[str, str] = {}
        if cred_ids:
            for cred in session.execute(
                select(AiAccountCredential).where(AiAccountCredential.id.in_(cred_ids))
            ).scalars():
                cred_to_account[cred.id] = cred.account_id
        account_ids = set(cred_to_account.values())
        accounts: dict[str, AiAccount] = {}
        plans: dict[str, str] = {}
        members: dict[str, str] = {}
        if account_ids:
            for acct in session.execute(
                select(AiAccount).where(AiAccount.id.in_(account_ids))
            ).scalars():
                accounts[acct.id] = acct
            plan_ids = {a.plan_id for a in accounts.values()}
            if plan_ids:
                for plan in session.execute(select(AiPlan).where(AiPlan.id.in_(plan_ids))).scalars():
                    plans[plan.id] = plan.plan_name
            member_ids = {a.primary_member_id for a in accounts.values() if a.primary_member_id}
            if member_ids:
                for m in session.execute(select(Member).where(Member.id.in_(member_ids))).scalars():
                    members[m.id] = m.display_name

        def _primary_name(acct: AiAccount | None) -> str | None:
            if not acct or not acct.primary_member_id:
                return None
            return members.get(acct.primary_member_id)

        by_account_map: dict[str, dict] = {}
        for u in all_rows:
            account_id = cred_to_account.get(u.credential_id) if u.credential_id else None
            bucket_key = account_id or "__unknown__"
            if bucket_key not in by_account_map:
                acct = accounts.get(account_id) if account_id else None
                by_account_map[bucket_key] = {
                    "account_id": account_id,
                    "account_identifier": acct.account_identifier if acct else "未知账号",
                    "primary_member_name": _primary_name(acct),
                    "plan_name": plans.get(acct.plan_id) if acct else None,
                    "request_count": 0,
                    "total_tokens": 0,
                    "cost_cents": 0,
                }
            row = by_account_map[bucket_key]
            row["request_count"] += 1
            row["total_tokens"] += int(u.total_tokens or 0)
            row["cost_cents"] += int(u.cost_cents or 0)

        by_account = sorted(
            by_account_map.values(),
            key=lambda r: r["total_tokens"],
            reverse=True,
        )

        by_model_map: dict[str, dict] = {}
        for u in all_rows:
            label = (u.model or "").strip() or "（未知）"
            bucket = by_model_map.get(label)
            if bucket is None:
                bucket = {
                    "model": label,
                    "request_count": 0,
                    "total_tokens": 0,
                    "cost_cents": 0,
                }
                by_model_map[label] = bucket
            bucket["request_count"] += 1
            bucket["total_tokens"] += int(u.total_tokens or 0)
            bucket["cost_cents"] += int(u.cost_cents or 0)
        by_model = sorted(
            by_model_map.values(),
            key=lambda r: r["cost_cents"],
            reverse=True,
        )

        items = []
        for u in all_rows[:limit]:
            account_id = cred_to_account.get(u.credential_id) if u.credential_id else None
            acct = accounts.get(account_id) if account_id else None
            items.append(
                {
                    "id": u.id,
                    "credential_id": u.credential_id,
                    "account_id": account_id,
                    "account_identifier": acct.account_identifier if acct else None,
                    "primary_member_name": _primary_name(acct),
                    "model": u.model,
                    "tokens_input": u.tokens_input,
                    "tokens_output": u.tokens_output,
                    "tokens_cache_read": u.tokens_cache_read,
                    "tokens_cache_write": u.tokens_cache_write,
                    "tokens_reasoning": u.tokens_reasoning,
                    "total_tokens": u.total_tokens,
                    "cost_cents": u.cost_cents,
                    "ts": u.ts.isoformat() if u.ts else None,
                }
            )
        return {"by_account": by_account, "by_model": by_model, "items": items}

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
        active_counts: dict[str, int] = {aid: 0 for aid in account_ids}
        for c in creds:
            if c.status == "active" and c.key_role == "primary":
                active_counts[c.account_id] = active_counts.get(c.account_id, 0) + 1
        return [
            {
                "id": a.id,
                "account_identifier": a.account_identifier,
                "plan_name": plans.get(a.plan_id),
                "status": a.status,
                "primary_member_name": members.get(a.primary_member_id) if a.primary_member_id else None,
                "active_credential_count": active_counts.get(a.id, 0),
                "proxy_enabled": bool(a.proxy_enabled),
            }
            for a in accounts
        ]

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
        account.proxy_enabled = body.proxy_enabled
        account.updated_at = proxy_service._utcnow()
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
        account.proxy_enabled = body.proxy_enabled
        account.updated_at = proxy_service._utcnow()
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
