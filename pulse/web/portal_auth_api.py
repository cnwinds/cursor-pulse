from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.web.auth_routes import auth_response, member_payload
from pulse.web.deps import require_portal_user
from pulse.web.schemas import DingTalkCallbackBody, PasswordLoginBody
from pulse.web.settings_store import effective_config_for_tenant


def register_portal_auth_routes(app, config: AppConfig, get_db, team_repo_fn):
    @app.get("/api/auth/me")
    def auth_me(
        session: Session = Depends(get_db),
        authorization: Annotated[str | None, Header()] = None,
    ):
        user = require_portal_user(config, session, authorization)
        return member_payload(user.member)

    @app.get("/api/auth/dingtalk/login-url")
    def dingtalk_login_url(session: Session = Depends(get_db)):
        from pulse.web.dingtalk_oauth import DingTalkOAuthError, build_login_url

        runtime = effective_config_for_tenant(session, config)
        try:
            url, state = build_login_url(runtime)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"url": url, "state": state}

    @app.post("/api/auth/dingtalk/callback")
    def dingtalk_callback(body: DingTalkCallbackBody, session: Session = Depends(get_db)):
        from fastapi.responses import JSONResponse

        from pulse.web.dingtalk_oauth import DingTalkOAuthError, exchange_code_for_userid
        from pulse.web.permissions import can_access_portal
        from pulse.web.portal import reconcile_oauth_member

        runtime = effective_config_for_tenant(session, config)
        try:
            userid, name = exchange_code_for_userid(runtime, body.code)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _team, repo = team_repo_fn(session)
        member = reconcile_oauth_member(repo, enterprise_userid=userid, display_name=name)
        if member is None:
            member = repo.get_or_create_member(userid, name)
            member.portal_status = "pending"
            session.flush()
        elif name and member.display_name != name:
            member.display_name = name

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
                        "dingtalk_user_id": member.dingtalk_user_id,
                    },
                },
            )

        member.last_portal_login_at = datetime.now(timezone.utc)
        session.commit()
        return auth_response(config, member)

    @app.post("/api/auth/login")
    def password_login(body: PasswordLoginBody, session: Session = Depends(get_db)):
        import hmac

        from pulse.web.portal import ADMIN_LOGIN_USERNAME, ensure_admin_member

        if body.username != ADMIN_LOGIN_USERNAME:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        if not config.web.admin_password:
            raise HTTPException(status_code=503, detail="未配置超管密码（ADMIN_PASSWORD）")
        if not hmac.compare_digest(body.password, config.web.admin_password):
            raise HTTPException(status_code=401, detail="账号或密码错误")

        _team, repo = team_repo_fn(session)
        member = ensure_admin_member(repo)
        member.last_portal_login_at = datetime.now(timezone.utc)
        session.commit()
        return auth_response(config, member)
