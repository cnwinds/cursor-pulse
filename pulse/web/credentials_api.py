from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.sync import CursorSyncService
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.audit import log_admin_action
from pulse.web.deps import PortalUser
from pulse.web.permissions import has_permission
from pulse.web.schemas import BindCredentialBody


def _encryption_key(config) -> str:
    key = (config.credentials.encryption_key or "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="未配置凭证加密密钥（PULSE_CREDENTIAL_ENCRYPTION_KEY）",
        )
    return key


def _get_team_account(session: Session, team_id: str, account_id: str):
    repo = ToolCenterRepository(session, team_id)
    account = repo.get_account(account_id)
    if not account or account.team_id != team_id:
        raise HTTPException(status_code=404, detail="账号不存在")
    if not account.vendor or account.vendor.slug != "cursor":
        raise HTTPException(status_code=400, detail="仅 Cursor 账号支持 API Key 绑定")
    return account, repo


def _can_manage_credential(user: PortalUser, account) -> bool:
    if has_permission(user.member, "accounts:write"):
        return True
    return account.primary_member_id == user.member.id


def _credential_status_payload(cred) -> dict:
    return {
        "bound": True,
        "key_hint": cred.key_hint,
        "status": cred.status,
        "sync_enabled": cred.sync_enabled,
        "bound_at": cred.bound_at.isoformat() if cred.bound_at else None,
        "last_sync_at": cred.last_sync_at.isoformat() if cred.last_sync_at else None,
        "last_sync_status": cred.last_sync_status,
        "last_sync_error": cred.last_sync_error,
    }


def register_credentials_routes(app, get_db, require_capability, team_repo_fn, config, require_user=None):
    if require_user is None:
        require_user = require_capability("accounts:read")

    @app.post("/api/v2/accounts/{account_id}/credentials")
    def bind_credential(
        account_id: str,
        body: BindCredentialBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_user),
    ):
        team, _ = team_repo_fn(session)
        account, _repo = _get_team_account(session, team.id, account_id)
        if not _can_manage_credential(user, account):
            raise HTTPException(status_code=403, detail="仅主使用人或管理员可绑定 API Key")

        api_key = body.api_key.strip()
        if not api_key.startswith("crsr_"):
            raise HTTPException(status_code=400, detail="API Key 须以 crsr_ 开头")

        enc_key = _encryption_key(config)
        cred_service = CredentialService(session, enc_key)
        try:
            cred = cred_service.bind_cursor_api_key(
                account_id=account_id,
                api_key=api_key,
                member_id=user.member.id,
            )
            sync_service = CursorSyncService(session, enc_key)
            sync_result = sync_service.sync_account(account_id, channel="web")
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="credential.bind",
                capability="accounts:read",
                detail=f"{account_id}:{cred.key_hint}",
            )
            session.commit()
            cred = cred_service.get_credential(account_id)
            return {
                **_credential_status_payload(cred),
                "sync": {
                    "ingestion_id": sync_result.ingestion_id,
                    "event_count": sync_result.event_count,
                    "status": sync_result.status,
                },
            }
        except HTTPException:
            raise
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/v2/accounts/{account_id}/credentials")
    def revoke_credential(
        account_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_user),
    ):
        team, _ = team_repo_fn(session)
        account, _repo = _get_team_account(session, team.id, account_id)
        if not _can_manage_credential(user, account):
            raise HTTPException(status_code=403, detail="仅主使用人或管理员可解绑 API Key")

        enc_key = _encryption_key(config)
        cred_service = CredentialService(session, enc_key)
        cred = cred_service.get_credential(account_id)
        if not cred or cred.status == "revoked":
            raise HTTPException(status_code=404, detail="该账号未绑定 API Key")

        cred_service.revoke(account_id)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="credential.revoke",
            capability="accounts:read",
            detail=account_id,
        )
        session.commit()
        return {"ok": True, "account_id": account_id}

    @app.get("/api/v2/accounts/{account_id}/credentials")
    def get_credential_status(
        account_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_user),
    ):
        team, _ = team_repo_fn(session)
        account, _repo = _get_team_account(session, team.id, account_id)
        if not _can_manage_credential(user, account):
            raise HTTPException(status_code=403, detail="仅主使用人或管理员可查看绑定状态")

        enc_key = _encryption_key(config)
        cred_service = CredentialService(session, enc_key)
        cred = cred_service.get_credential(account_id)
        if not cred or cred.status == "revoked":
            return {
                "bound": False,
                "key_hint": None,
                "last_sync_at": None,
                "last_sync_status": "never",
            }
        return _credential_status_payload(cred)

    @app.post(
        "/api/v2/accounts/{account_id}/sync",
        dependencies=[Depends(require_capability("accounts:write"))],
    )
    def trigger_sync(
        account_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("accounts:write")),
    ):
        team, _ = team_repo_fn(session)
        account, _repo = _get_team_account(session, team.id, account_id)

        enc_key = _encryption_key(config)
        cred_service = CredentialService(session, enc_key)
        cred = cred_service.get_credential(account_id)
        if not cred or cred.status != "active":
            raise HTTPException(status_code=400, detail="账号未绑定有效的 API Key")

        try:
            sync_service = CursorSyncService(session, enc_key)
            result = sync_service.sync_account(account_id, channel="web")
            log_admin_action(
                session,
                team_id=team.id,
                member_id=user.member.id,
                action="credential.sync",
                capability="accounts:write",
                detail=f"{account_id}:{result.event_count} events",
            )
            session.commit()
            cred = cred_service.get_credential(account_id)
            return {
                "ok": True,
                "account_id": account_id,
                "ingestion_id": result.ingestion_id,
                "event_count": result.event_count,
                "status": result.status,
                "last_sync_at": cred.last_sync_at.isoformat() if cred.last_sync_at else None,
                "last_sync_status": cred.last_sync_status,
            }
        except Exception as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
