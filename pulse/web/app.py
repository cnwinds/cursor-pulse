from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pulse.aggregate.engine import aggregate_period
from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.report.service import get_latest_snapshot
from pulse.storage.models import AlertLog, Member, QueryLog
from pulse.tenant.context import team_repository
from pulse.web.auth_routes import auth_response, member_payload
from pulse.web.audit import list_admin_audit_logs, log_admin_action
from pulse.web.deps import PortalUser, require_portal_user
from pulse.web.memory_api import MemoryQueryService
from pulse.web.permissions import (
    ALL_PERMISSIONS,
    PORTAL_ROLE_DESCRIPTIONS,
    PORTAL_ROLE_LABELS,
    ROLE_PERMISSIONS,
    has_permission,
)
from pulse.web.schemas import (
    ChatBody,
    DingTalkCallbackBody,
    PasswordLoginBody,
    PortalApproveBody,
    PrincipleCreateBody,
    SettingsPatchBody,
)
from pulse.web.dashboard_api import (
    build_dashboard_overview,
    build_integrations_status,
    build_schedule_plan,
)
from pulse.storage.repository import input_type_from_source_type
from pulse.web.accounts_api import register_accounts_v2_routes
from pulse.web.submission_status_api import register_submission_status_routes
from pulse.web.knowledge_api import register_knowledge_routes
from pulse.web.usage_api import register_usage_routes
from pulse.web.requests_api import register_access_request_routes
from pulse.web.settings_store import EDITABLE_SECTIONS, patch_team_setting, settings_for_api


def create_app(config: AppConfig, session_factory: sessionmaker[Session]) -> FastAPI:
    app = FastAPI(title="Cursor Pulse Admin", version="0.2.0")

    if config.web.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.web.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def get_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def _team_repo(session: Session):
        return team_repository(session, config)

    def require_capability(capability: str):
        def _dep(
            session: Session = Depends(get_db),
            authorization: Annotated[str | None, Header()] = None,
        ) -> PortalUser:
            user = require_portal_user(config, session, authorization)
            if not has_permission(user.member, capability):
                raise HTTPException(status_code=403, detail=f"缺少权限: {capability}")
            return user

        return _dep

    def _require_user(
        session: Session = Depends(get_db),
        authorization: Annotated[str | None, Header()] = None,
    ) -> PortalUser:
        return require_portal_user(config, session, authorization)

    @app.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

    @app.get("/", response_class=HTMLResponse)
    def dashboard():
        return DASHBOARD_HTML

    @app.get("/api/auth/me")
    def auth_me(
        session: Session = Depends(get_db),
        authorization: Annotated[str | None, Header()] = None,
    ):
        user = require_portal_user(config, session, authorization)
        return member_payload(user.member)

    @app.get("/api/auth/dingtalk/login-url")
    def dingtalk_login_url():
        from pulse.web.dingtalk_oauth import DingTalkOAuthError, build_login_url

        try:
            url, state = build_login_url(config)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"url": url, "state": state}

    @app.post("/api/auth/dingtalk/callback")
    def dingtalk_callback(body: DingTalkCallbackBody, session: Session = Depends(get_db)):
        from fastapi.responses import JSONResponse

        from pulse.web.dingtalk_oauth import DingTalkOAuthError, exchange_code_for_userid
        from pulse.web.permissions import can_access_portal

        try:
            userid, name = exchange_code_for_userid(config, body.code)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _team, repo = _team_repo(session)
        from pulse.web.portal import reconcile_oauth_member

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

        _team, repo = _team_repo(session)
        member = ensure_admin_member(repo)
        member.last_portal_login_at = datetime.now(timezone.utc)
        session.commit()
        return auth_response(config, member)

    @app.get(
        "/api/periods/{period}/status",
        dependencies=[Depends(require_capability("submissions:read"))],
    )
    def period_status(period: str, session: Session = Depends(get_db)):
        _team, repo = _team_repo(session)
        active = repo.list_active_members()
        submitted = repo.get_submitted_member_ids(period)
        return {
            "period": period,
            "active_count": len(active),
            "submitted_count": len(submitted),
            "members": [
                {
                    "display_name": m.display_name,
                    "dingtalk_user_id": m.dingtalk_user_id,
                    "submitted": m.id in submitted,
                    "status": m.status,
                }
                for m in active
            ],
            "unsubmitted": [m.display_name for m in repo.get_unsubmitted_members(period)],
        }

    @app.get("/api/periods/{period}/metrics")
    def period_metrics(
        period: str,
        session: Session = Depends(get_db),
        refresh: bool = Query(False),
        authorization: Annotated[str | None, Header()] = None,
    ):
        user = require_portal_user(config, session, authorization)
        if not has_permission(user.member, "metrics:read"):
            raise HTTPException(status_code=403, detail="缺少权限: metrics:read")
        if refresh and not has_permission(user.member, "metrics:aggregate"):
            raise HTTPException(status_code=403, detail="缺少权限: metrics:aggregate")
        team, _repo = _team_repo(session)
        if refresh:
            metrics = aggregate_period(session, period, team_id=team.id)
            session.commit()
        else:
            snap = get_latest_snapshot(session, period, team_id=team.id)
            if not snap:
                raise HTTPException(404, detail=f"账期 {period} 无聚合快照")
            metrics = snap.metrics_json
        return metrics

    @app.get("/api/query-logs", dependencies=[Depends(require_capability("audit:read"))])
    def query_logs(session: Session = Depends(get_db), limit: int = Query(50, le=200)):
        rows = session.scalars(
            select(QueryLog).order_by(QueryLog.created_at.desc()).limit(limit)
        ).all()
        return [
            {
                "id": row.id,
                "question": row.question,
                "query_plan": row.query_plan,
                "answer": row.answer,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    @app.get("/api/alerts", dependencies=[Depends(require_capability("audit:read"))])
    def alerts(session: Session = Depends(get_db), limit: int = Query(50, le=200)):
        team, _repo = _team_repo(session)
        rows = session.scalars(
            select(AlertLog)
            .where(AlertLog.team_id == team.id)
            .order_by(AlertLog.created_at.desc())
            .limit(limit)
        ).all()
        return [
            {
                "period": row.period,
                "alert_type": row.alert_type,
                "severity": row.severity,
                "message": row.message,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]

    @app.get(
        "/api/pending-reviews",
        dependencies=[Depends(require_capability("submissions:read"))],
    )
    def pending_reviews(session: Session = Depends(get_db), period: str | None = Query(None)):
        _team, repo = _team_repo(session)
        rows = repo.list_pending_ingestions(period)
        return [
            {
                "id": ing.id,
                "id_prefix": ing.id[:8],
                "member_id": ing.member_id,
                "period": ing.billing_period,
                "source_type": ing.source_type,
                "input_type": input_type_from_source_type(ing.source_type),
                "ingested_at": ing.ingested_at.isoformat(),
                "submitted_at": ing.ingested_at.isoformat(),
            }
            for ing in rows
        ]

    @app.get("/api/export/{period}", dependencies=[Depends(require_capability("metrics:read"))])
    def export_bi(period: str, session: Session = Depends(get_db)):
        from pulse.integrations.webhook import build_bi_payload

        team, _repo = _team_repo(session)
        snap = get_latest_snapshot(session, period, team_id=team.id)
        if not snap:
            raise HTTPException(404, detail=f"账期 {period} 无聚合快照")
        return build_bi_payload(
            team_slug=team.slug,
            team_name=team.name,
            period=period,
            metrics=snap.metrics_json,
        )

    @app.get("/api/config/summary", dependencies=[Depends(require_capability("settings:read"))])
    def config_summary(session: Session = Depends(get_db)):
        team, _repo = _team_repo(session)
        effective = settings_for_api(config, session, team.id)
        return {
            "current_period": current_period(config),
            "team_slug": config.tenant.slug,
            "timezone": effective["collection"]["timezone"],
            "group_configured": bool(config.dingtalk.group_open_conversation_id),
            "llm_report": effective["llm"]["enabled"],
            "llm_vision": effective["llm"]["vision_enabled"],
            "alerts_enabled": effective["alerts"]["enabled"],
            "bi_webhook": bool(effective["integrations"]["webhook_url"]),
        }

    @app.get("/api/settings", dependencies=[Depends(require_capability("settings:read"))])
    def get_settings(session: Session = Depends(get_db)):
        team, _repo = _team_repo(session)
        return settings_for_api(config, session, team.id)

    @app.patch(
        "/api/settings/{section}",
        dependencies=[Depends(require_capability("settings:write"))],
    )
    def patch_settings(
        section: str,
        body: SettingsPatchBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("settings:write")),
    ):
        team, _repo = _team_repo(session)
        if section not in EDITABLE_SECTIONS:
            raise HTTPException(400, detail=f"不可编辑的配置分区: {section}")
        try:
            patch_team_setting(
                session,
                team_id=team.id,
                section=section,
                patch=body.data,
                member_id=user.member.id,
            )
            session.commit()
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        return settings_for_api(config, session, team.id)

    def _memory_svc(session: Session, team_id: str) -> MemoryQueryService:
        return MemoryQueryService(session, team_id)

    def _chat_messenger():
        if not config.dingtalk.app_key or not config.dingtalk.app_secret:
            return None
        try:
            from pulse.bot.dingtalk.messenger import DingTalkMessenger

            return DingTalkMessenger(config)
        except Exception:
            return None

    @app.post("/api/chat")
    def chat_with_xiaomai(
        body: ChatBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(_require_user),
    ):
        from pulse.chat.service import ChatService

        if not body.message.strip():
            raise HTTPException(400, detail="消息不能为空")
        team, repo = _team_repo(session)
        service = ChatService(
            config,
            session_factory=session_factory,
            messenger=_chat_messenger(),
        )
        result = service.chat(
            session=session,
            team=team,
            repo=repo,
            member=user.member,
            message=body.message.strip(),
            channel="web",
            is_group=False,
            display_name=user.member.display_name,
        )
        session.commit()
        return {
            "reply": result.reply,
            "actions": [
                {
                    "tool": a.tool,
                    "status": a.status,
                    "message": a.message,
                    "capability": a.capability,
                }
                for a in result.actions
            ],
        }

    @app.get("/api/memory/atoms", dependencies=[Depends(require_capability("memory:read"))])
    def memory_atoms(
        session: Session = Depends(get_db),
        subject_id: str | None = Query(None),
        q: str | None = Query(None),
    ):
        team, _ = _team_repo(session)
        return _memory_svc(session, team.id).list_atoms(subject_id=subject_id, q=q)

    @app.get("/api/memory/commitments", dependencies=[Depends(require_capability("memory:read"))])
    def memory_commitments(
        session: Session = Depends(get_db),
        counterparty_id: str | None = Query(None),
    ):
        team, _ = _team_repo(session)
        return _memory_svc(session, team.id).list_commitments(counterparty_id=counterparty_id)

    @app.get("/api/memory/principles", dependencies=[Depends(require_capability("memory:read"))])
    def memory_principles(session: Session = Depends(get_db)):
        team, _ = _team_repo(session)
        return _memory_svc(session, team.id).list_principles()

    @app.post("/api/memory/principles", dependencies=[Depends(require_capability("memory:write"))])
    def create_principle(
        body: PrincipleCreateBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("memory:write")),
    ):
        team, _ = _team_repo(session)
        if body.tier not in ("bottom_line", "learned"):
            raise HTTPException(400, detail="tier 须为 bottom_line 或 learned")
        svc = _memory_svc(session, team.id)
        result = svc.add_principle(rule=body.rule, tier=body.tier, origin=body.origin)
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="memory.principle.create",
            capability="memory:write",
            detail=body.rule[:200],
        )
        session.commit()
        return result

    @app.get("/api/memory/disclosure", dependencies=[Depends(require_capability("memory:read"))])
    def memory_disclosure(session: Session = Depends(get_db), limit: int = Query(50, le=200)):
        team, _ = _team_repo(session)
        return _memory_svc(session, team.id).list_disclosure(limit=limit)

    @app.get("/api/memory/evolution", dependencies=[Depends(require_capability("memory:read"))])
    def memory_evolution(session: Session = Depends(get_db), limit: int = Query(50, le=200)):
        team, _ = _team_repo(session)
        return _memory_svc(session, team.id).list_evolution(limit=limit)

    @app.post("/api/memory/evolution/run", dependencies=[Depends(require_capability("evolution:run"))])
    def run_evolution(
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("evolution:run")),
    ):
        from pulse.memory_adapter.evolution_job import run_memory_evolution

        team, _ = _team_repo(session)
        team_id = team.id
        actor_id = user.member.id
        result = run_memory_evolution(session_factory, config)
        log_admin_action(
            session,
            team_id=team_id,
            member_id=actor_id,
            action="memory.evolution.run",
            capability="evolution:run",
            detail=str(result),
        )
        session.commit()
        return result

    @app.get("/api/audit-logs", dependencies=[Depends(require_capability("audit:read"))])
    def audit_logs(session: Session = Depends(get_db), limit: int = Query(100, le=500)):
        team, _ = _team_repo(session)
        portal_logs = list_admin_audit_logs(session, team.id, limit=limit)
        query_rows = session.scalars(
            select(QueryLog).order_by(QueryLog.created_at.desc()).limit(limit)
        ).all()
        alert_rows = session.scalars(
            select(AlertLog)
            .where(AlertLog.team_id == team.id)
            .order_by(AlertLog.created_at.desc())
            .limit(limit)
        ).all()
        return {
            "admin_actions": portal_logs,
            "query_logs": [
                {
                    "id": row.id,
                    "question": row.question,
                    "answer": row.answer,
                    "created_at": row.created_at.isoformat(),
                }
                for row in query_rows
            ],
            "alerts": [
                {
                    "period": row.period,
                    "alert_type": row.alert_type,
                    "severity": row.severity,
                    "message": row.message,
                    "created_at": row.created_at.isoformat(),
                }
                for row in alert_rows
            ],
        }

    def _portal_user_row(member: Member) -> dict:
        return {
            "id": member.id,
            "display_name": member.display_name,
            "dingtalk_user_id": member.dingtalk_user_id,
            "portal_status": member.portal_status,
            "portal_role": member.portal_role,
            "portal_permissions": member.portal_permissions,
            "last_portal_login_at": (
                member.last_portal_login_at.isoformat() if member.last_portal_login_at else None
            ),
            "created_at": member.created_at.isoformat(),
        }

    @app.get("/api/portal/roles", dependencies=[Depends(require_capability("admin:users"))])
    def portal_roles():
        return [
            {
                "id": role,
                "label": PORTAL_ROLE_LABELS.get(role, role),
                "description": PORTAL_ROLE_DESCRIPTIONS.get(role, ""),
                "permissions": sorted(ROLE_PERMISSIONS.get(role, frozenset())),
            }
            for role in ("owner", "operator", "auditor", "ai_member", "custom")
        ]

    @app.get(
        "/api/portal/users/pending",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_pending_users(session: Session = Depends(get_db)):
        from pulse.web.portal import list_pending_portal_users

        team, _ = _team_repo(session)
        return [_portal_user_row(m) for m in list_pending_portal_users(session, team.id)]

    @app.get(
        "/api/portal/users",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_active_users(session: Session = Depends(get_db)):
        from pulse.web.portal import list_portal_users

        team, _ = _team_repo(session)
        return [_portal_user_row(m) for m in list_portal_users(session, team.id)]

    @app.post(
        "/api/portal/users/{member_id}/approve",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_approve_user(
        member_id: str,
        body: PortalApproveBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, approve_portal_user

        if body.portal_role not in ROLE_PERMISSIONS and body.portal_role != "custom":
            raise HTTPException(400, detail="无效的 portal_role")
        perms = None
        if body.portal_role == "custom":
            perms = [p for p in (body.portal_permissions or []) if p in ALL_PERMISSIONS]
        team, _ = _team_repo(session)
        try:
            member = approve_portal_user(
                session,
                team.id,
                member_id,
                role=body.portal_role,
                permissions=perms,
            )
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.approve",
            capability="admin:users",
            detail=f"{member.dingtalk_user_id} -> {member.portal_role}",
        )
        session.commit()
        return _portal_user_row(member)

    @app.post(
        "/api/portal/users/{member_id}/reject",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_reject_user(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, reject_portal_user

        team, _ = _team_repo(session)
        try:
            member = reject_portal_user(session, team.id, member_id)
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.reject",
            capability="admin:users",
            detail=member.dingtalk_user_id,
        )
        session.commit()
        return _portal_user_row(member)

    @app.post(
        "/api/portal/users/{member_id}/disable",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_disable_user(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, disable_portal_user

        team, _ = _team_repo(session)
        if member_id == user.member.id:
            raise HTTPException(400, detail="不能禁用当前登录账号")
        try:
            member = disable_portal_user(session, team.id, member_id)
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.disable",
            capability="admin:users",
            detail=member.dingtalk_user_id,
        )
        session.commit()
        return _portal_user_row(member)

    @app.delete(
        "/api/portal/users/{member_id}",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def portal_delete_user(
        member_id: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        from pulse.web.portal import PortalAdminError, delete_member_without_ingestions

        team, _ = _team_repo(session)
        member = session.get(Member, member_id)
        if member is None or member.team_id != team.id:
            raise HTTPException(404, detail="成员不存在")
        if member_id == user.member.id:
            raise HTTPException(400, detail="不能删除当前登录账号")
        try:
            deleted = delete_member_without_ingestions(session, team.id, member.dingtalk_user_id)
        except PortalAdminError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="portal.user.delete",
            capability="admin:users",
            detail=deleted.dingtalk_user_id,
        )
        session.commit()
        return {"ok": True, "id": member_id}

    @app.get("/api/dashboard/overview", dependencies=[Depends(require_capability("settings:read"))])
    def dashboard_overview(session: Session = Depends(get_db)):
        team, repo = _team_repo(session)
        return build_dashboard_overview(config, session, team.id, repo=repo)

    @app.get("/api/system/schedule", dependencies=[Depends(require_capability("settings:read"))])
    def system_schedule(session: Session = Depends(get_db)):
        team, _ = _team_repo(session)
        return build_schedule_plan(config, session, team.id)

    @app.get("/api/system/integrations", dependencies=[Depends(require_capability("settings:read"))])
    def system_integrations(session: Session = Depends(get_db)):
        team, _ = _team_repo(session)
        return build_integrations_status(config, session, team.id)

    register_accounts_v2_routes(app, get_db, require_capability, _team_repo)
    register_submission_status_routes(app, get_db, require_capability, _team_repo)
    register_access_request_routes(app, get_db, require_capability, _team_repo, config)
    register_knowledge_routes(app, get_db, require_capability, _team_repo, config)
    register_usage_routes(app, get_db, require_capability, _team_repo, config)

    _mount_admin_static(app)
    return app


def _mount_admin_static(app: FastAPI) -> None:
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    candidates = [
        Path(__file__).resolve().parents[2] / "web-admin" / "dist",
        Path(__file__).parent / "static",
    ]
    for static_dir in candidates:
        if static_dir.is_dir() and (static_dir / "index.html").exists():
            app.mount(
                "/admin",
                StaticFiles(directory=str(static_dir), html=True),
                name="admin_spa",
            )
            break


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Cursor Pulse Admin</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 960px; }
    input, button { padding: 0.4rem 0.6rem; margin: 0.25rem 0; }
    pre { background: #f4f4f5; padding: 1rem; overflow: auto; border-radius: 8px; }
    .row { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
    th, td { border: 1px solid #ddd; padding: 0.5rem; text-align: left; }
    th { background: #fafafa; }
  </style>
</head>
<body>
  <h1>Cursor Pulse 管理后台</h1>
  <p>推荐使用 Vue 前端（<code>web-admin/</code>）。本页为兼容旧版 Token 的简易面板。</p>
  <div class="row">
    <label>Admin Token <input id="token" type="password" placeholder="Bearer token" /></label>
    <label>账期 <input id="period" type="text" placeholder="2026-06" /></label>
    <button onclick="loadAll()">刷新</button>
  </div>
  <h2>提交进度</h2>
  <div id="status"></div>
  <h2>指标快照</h2>
  <pre id="metrics">（加载中…）</pre>
  <h2>最近查询</h2>
  <pre id="queries">（加载中…）</pre>
  <script>
    function headers() {
      const t = document.getElementById('token').value.trim();
      return t ? { 'Authorization': 'Bearer ' + t } : {};
    }
    async function api(path) {
      const res = await fetch(path, { headers: headers() });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }
    function renderStatus(data) {
      const rows = data.members.map(m =>
        `<tr><td>${m.display_name}</td><td>${m.status}</td><td>${m.submitted ? '✅' : '❌'}</td></tr>`
      ).join('');
      document.getElementById('status').innerHTML =
        `<p>已提交 ${data.submitted_count}/${data.active_count}</p>
         <table><tr><th>姓名</th><th>状态</th><th>提交</th></tr>${rows}</table>`;
    }
    async function loadAll() {
      try {
        const cfg = await api('/api/config/summary');
        const periodInput = document.getElementById('period');
        if (!periodInput.value) periodInput.value = cfg.current_period;
        const period = periodInput.value;
        const [status, metrics, queries] = await Promise.all([
          api('/api/periods/' + period + '/status'),
          api('/api/periods/' + period + '/metrics'),
          api('/api/query-logs?limit=20'),
        ]);
        renderStatus(status);
        document.getElementById('metrics').textContent = JSON.stringify(metrics, null, 2);
        document.getElementById('queries').textContent = JSON.stringify(queries, null, 2);
      } catch (e) {
        alert('加载失败：' + e.message);
      }
    }
    loadAll();
  </script>
</body>
</html>
"""
