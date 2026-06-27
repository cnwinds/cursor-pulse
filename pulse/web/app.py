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
from pulse.web.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS, has_permission
from pulse.web.schemas import (
    ChatBody,
    DingTalkCallbackBody,
    PasswordLoginBody,
    PortalGrantBody,
    PrincipleCreateBody,
    SettingsPatchBody,
)
from pulse.web.dashboard_api import (
    build_dashboard_overview,
    build_integrations_status,
    build_schedule_plan,
)
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
        from pulse.web.dingtalk_oauth import DingTalkOAuthError, exchange_code_for_userid
        from pulse.web.permissions import can_access_portal

        try:
            userid, name = exchange_code_for_userid(config, body.code)
        except DingTalkOAuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        _team, repo = _team_repo(session)
        member = repo.get_member_by_dingtalk_id(userid)
        if member is None:
            member = repo.get_or_create_member(userid, name)
            session.flush()

        from pulse.web.permissions import can_access_portal

        if not can_access_portal(member):
            raise HTTPException(status_code=403, detail="该账号无后台访问权限")

        member.last_portal_login_at = datetime.now(timezone.utc)
        if name and member.display_name != name:
            member.display_name = name
        session.commit()
        return auth_response(config, member)

    @app.post("/api/auth/login")
    def password_login(body: PasswordLoginBody, session: Session = Depends(get_db)):
        from pulse.web.passwords import verify_password
        from pulse.web.permissions import can_access_portal

        team, _repo = _team_repo(session)
        member = session.scalar(
            select(Member).where(
                Member.team_id == team.id,
                Member.dingtalk_user_id == body.dingtalk_user_id,
            )
        )
        if member is None or not member.password_hash:
            raise HTTPException(status_code=401, detail="账号或密码错误")
        if not verify_password(body.password, member.password_hash):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        if not can_access_portal(member):
            raise HTTPException(status_code=403, detail="该账号无后台访问权限")

        member.last_portal_login_at = datetime.now(timezone.utc)
        session.commit()
        return auth_response(config, member)

    @app.get("/api/members", dependencies=[Depends(require_capability("members:read"))])
    def list_members(session: Session = Depends(get_db)):
        team, _repo = _team_repo(session)
        members = session.scalars(
            select(Member).where(Member.team_id == team.id).order_by(Member.display_name)
        ).all()
        return [
            {
                "id": m.id,
                "display_name": m.display_name,
                "dingtalk_user_id": m.dingtalk_user_id,
                "status": m.status,
                "portal_role": m.portal_role,
                "portal_permissions": m.portal_permissions,
                "last_portal_login_at": (
                    m.last_portal_login_at.isoformat() if m.last_portal_login_at else None
                ),
                "created_at": m.created_at.isoformat(),
            }
            for m in members
        ]

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
        rows = repo.list_pending_submissions(period)
        return [
            {
                "id": sub.id,
                "id_prefix": sub.id[:8],
                "member_id": sub.member_id,
                "period": sub.billing_period,
                "input_type": sub.input_type,
                "submitted_at": sub.submitted_at.isoformat(),
            }
            for sub in rows
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

    @app.patch(
        "/api/members/{member_id}/portal",
        dependencies=[Depends(require_capability("admin:users"))],
    )
    def update_member_portal(
        member_id: str,
        body: PortalGrantBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("admin:users")),
    ):
        team, _ = _team_repo(session)
        member = session.get(Member, member_id)
        if member is None or member.team_id != team.id:
            raise HTTPException(404, detail="成员不存在")

        if body.portal_role is not None:
            if body.portal_role and body.portal_role not in ROLE_PERMISSIONS and body.portal_role != "custom":
                raise HTTPException(400, detail="无效的 portal_role")
            member.portal_role = body.portal_role or None
            if body.portal_role != "custom":
                member.portal_permissions = None

        if body.portal_permissions is not None:
            if member.portal_role != "custom":
                raise HTTPException(400, detail="仅 custom 角色可设置 portal_permissions")
            member.portal_permissions = [p for p in body.portal_permissions if p in ALL_PERMISSIONS]

        if body.display_name:
            member.display_name = body.display_name

        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="member.portal.update",
            capability="admin:users",
            detail=f"{member.dingtalk_user_id} -> {member.portal_role}",
        )
        session.commit()
        return {
            "id": member.id,
            "display_name": member.display_name,
            "dingtalk_user_id": member.dingtalk_user_id,
            "portal_role": member.portal_role,
            "portal_permissions": member.portal_permissions,
        }

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
  <h2>成员</h2>
  <div id="members"></div>
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
    function renderMembers(list) {
      const rows = list.map(m =>
        `<tr><td>${m.display_name}</td><td>${m.dingtalk_user_id}</td><td>${m.status}</td></tr>`
      ).join('');
      document.getElementById('members').innerHTML =
        `<table><tr><th>姓名</th><th>userid</th><th>状态</th></tr>${rows}</table>`;
    }
    async function loadAll() {
      try {
        const cfg = await api('/api/config/summary');
        const periodInput = document.getElementById('period');
        if (!periodInput.value) periodInput.value = cfg.current_period;
        const period = periodInput.value;
        const [status, members, metrics, queries] = await Promise.all([
          api('/api/periods/' + period + '/status'),
          api('/api/members'),
          api('/api/periods/' + period + '/metrics'),
          api('/api/query-logs?limit=20'),
        ]);
        renderStatus(status);
        renderMembers(members);
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
