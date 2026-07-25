from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.web.auth_providers import list_auth_providers
from pulse.web.auth_routes import auth_response, member_payload
from pulse.web.deps import require_portal_user
from pulse.web.schemas import DingTalkCallbackBody, FeishuCallbackBody, PasswordLoginBody
from pulse.web.settings_store import effective_config_for_tenant


def _oauth_pending_or_auth(*, config: AppConfig, session: Session, member):
    """Shared pending-approval / JWT response for IM OAuth callbacks."""
    from fastapi.responses import JSONResponse

    from pulse.web.permissions import can_access_portal

    if member.portal_status == "rejected":
        raise HTTPException(status_code=403, detail="你的账号已被拒绝，请联系超级管理员")
    if member.portal_status == "disabled":
        raise HTTPException(status_code=403, detail="你的账号已被禁用，请联系超级管理员")

    if not can_access_portal(member):
        if member.portal_status != "pending":
            member.portal_status = "pending"
        member.last_portal_login_at = datetime.now(timezone.utc)
        session.commit()
        return JSONResponse(
            status_code=202,
            content={
                "status": "pending",
                "message": "你的账号正在等待超级管理员审批",
                "user": {
                    "id": member.id,
                    "display_name": member.display_name,
                    "channel_user_id": member.channel_user_id,
                    "channel": getattr(member, "channel", None),
                },
            },
        )

    member.last_portal_login_at = datetime.now(timezone.utc)
    session.commit()
    return auth_response(config, member)


def register_portal_auth_routes(app, config: AppConfig, get_db, team_repo_fn):
    @app.get("/api/auth/me")
    def auth_me(
        session: Session = Depends(get_db),
        authorization: Annotated[str | None, Header()] = None,
    ):
        user = require_portal_user(config, session, authorization)
        return member_payload(user.member)

    @app.get("/api/auth/providers")
    def auth_providers(session: Session = Depends(get_db)):
        runtime = effective_config_for_tenant(session, config)
        return {"providers": list_auth_providers(runtime)}

    @app.get("/api/auth/dingtalk/login-url")
    def dingtalk_login_url(
        session: Session = Depends(get_db),
        redirect_uri: str | None = None,
    ):
        from pulse.web.dingtalk_oauth import (
            DingTalkOAuthError,
            build_login_url,
            resolve_oauth_redirect_uri,
        )

        runtime = effective_config_for_tenant(session, config)
        try:
            resolved = resolve_oauth_redirect_uri(runtime, redirect_uri)
            url, state = build_login_url(runtime, redirect_uri=resolved)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"url": url, "state": state, "redirect_uri": resolved}

    @app.post("/api/auth/dingtalk/callback")
    def dingtalk_callback(body: DingTalkCallbackBody, session: Session = Depends(get_db)):
        from pulse.web.dingtalk_oauth import DingTalkOAuthError, exchange_code_for_userid
        from pulse.web.portal import reconcile_oauth_member

        runtime = effective_config_for_tenant(session, config)
        try:
            userid, name = exchange_code_for_userid(runtime, body.code)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _team, repo = team_repo_fn(session)
        member = reconcile_oauth_member(repo, enterprise_userid=userid, display_name=name)
        if member is None:
            member = repo.get_or_create_member(userid, name, channel="dingtalk")
            member.channel = "dingtalk"
            member.portal_status = "pending"
            session.flush()
        else:
            member.channel = member.channel or "dingtalk"
            if name and member.display_name != name:
                member.display_name = name

        return _oauth_pending_or_auth(config=config, session=session, member=member)

    @app.get("/api/auth/feishu/login-url")
    def feishu_login_url(
        session: Session = Depends(get_db),
        redirect_uri: str | None = None,
    ):
        from pulse.web.dingtalk_oauth import resolve_oauth_redirect_uri
        from pulse.web.feishu_oauth import FeishuOAuthError, build_login_url

        runtime = effective_config_for_tenant(session, config)
        try:
            resolved = resolve_oauth_redirect_uri(runtime, redirect_uri)
            url, state = build_login_url(runtime, redirect_uri=resolved)
        except FeishuOAuthError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"url": url, "state": state, "redirect_uri": resolved}

    @app.post("/api/auth/feishu/callback")
    def feishu_callback(body: FeishuCallbackBody, session: Session = Depends(get_db)):
        from pulse.web.feishu_oauth import FeishuOAuthError, exchange_code_for_userid

        runtime = effective_config_for_tenant(session, config)
        try:
            userid, name = exchange_code_for_userid(
                runtime, body.code, redirect_uri=body.redirect_uri
            )
        except FeishuOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _team, repo = team_repo_fn(session)
        member = repo.get_or_create_member(userid, name, channel="feishu")
        member.channel = "feishu"
        if member.portal_status is None:
            member.portal_status = "pending"
        if name and member.display_name != name:
            member.display_name = name
        session.flush()

        return _oauth_pending_or_auth(config=config, session=session, member=member)

    @app.post("/api/auth/login")
    def password_login(body: PasswordLoginBody, session: Session = Depends(get_db)):
        import hmac

        from pulse.web.passwords import looks_like_password_hash, verify_password
        from pulse.web.portal import ADMIN_LOGIN_USERNAME, ensure_admin_member

        if body.username != ADMIN_LOGIN_USERNAME:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        if not config.web.admin_password:
            raise HTTPException(status_code=503, detail="未配置超管密码（ADMIN_PASSWORD）")
        stored = config.web.admin_password
        if looks_like_password_hash(stored):
            ok = verify_password(body.password, stored)
        else:
            ok = hmac.compare_digest(body.password, stored)
        if not ok:
            raise HTTPException(status_code=401, detail="账号或密码错误")

        _team, repo = team_repo_fn(session)
        member = ensure_admin_member(repo)
        member.last_portal_login_at = datetime.now(timezone.utc)
        session.commit()
        return auth_response(config, member)
