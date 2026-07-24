from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Annotated

from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pulse.aggregate.engine import aggregate_period
from pulse.config import AppConfig
from pulse.report.service import get_latest_snapshot
from pulse.storage.models import AlertLog, QueryLog
from pulse.tenant.context import team_repository
from pulse.web.audit import list_admin_audit_logs
from pulse.web.deps import PortalUser, require_portal_user
from pulse.web.permissions import has_permission
from pulse.web.schemas import ChatBody
from pulse.web.dashboard_api import (
    build_dashboard_overview,
    build_integrations_status,
    build_schedule_plan,
)
from pulse.web.accounts_api import register_accounts_v2_routes
from pulse.web.credentials_api import register_credentials_routes
from pulse.web.ingestion_status_api import register_ingestion_status_routes
from pulse.web.knowledge_api import register_knowledge_routes
from pulse.web.usage_api import register_usage_routes
from pulse.web.assistant_capabilities_api import register_assistant_capabilities_routes
from pulse.web.assistant_prompts_api import register_assistant_prompts_routes
from pulse.web.assistant_sessions_api import register_assistant_sessions_routes
from pulse.web.assistant_skills_api import register_assistant_skills_routes
from pulse.web.internal_capabilities_api import register_internal_capabilities_routes
from pulse.web.internal_channel_api import register_internal_channel_routes
from pulse.web.internal_proxy_api import register_internal_proxy_routes
from pulse.web.portal_auth_api import register_portal_auth_routes
from pulse.web.portal_users_api import register_portal_users_routes
from pulse.web.pricing_api import register_pricing_routes
from pulse.web.proxy_keys_api import register_proxy_keys_routes
from pulse.web.quota_api import register_quota_routes
from pulse.web.settings_api import register_settings_routes

logger = logging.getLogger(__name__)


def create_app(
    config: AppConfig,
    session_factory: sessionmaker[Session],
    *,
    require_admin_spa: bool = False,
) -> FastAPI:
    from pulse.security_tokens import assert_secure_service_tokens

    assert_secure_service_tokens(
        assistant_token=config.assistant_mirror.service_token,
        pulse_internal_token=config.internal.service_token,
    )
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

    admin_spa_dir = resolve_admin_static_dir()
    if require_admin_spa and admin_spa_dir is None:
        raise RuntimeError(
            "Vue admin SPA not found under pulse/web/static (index.html missing). "
            "Build it with: cd web-admin && npm ci && npm run build. "
            "Optional override: PULSE_ADMIN_STATIC_DIR=/path/to/dist"
        )

    @app.get("/")
    def dashboard():
        if admin_spa_dir is not None:
            return RedirectResponse(url="/admin/", status_code=307)
        return HTMLResponse(DASHBOARD_HTML)

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

    @app.post("/api/chat")
    def chat_with_xiaomai(
        body: ChatBody,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(_require_user),
    ):
        if not body.message.strip():
            raise HTTPException(400, detail="消息不能为空")
        team, _repo = _team_repo(session)
        message = body.message.strip()

        if not config.assistant_mirror.enabled:
            raise HTTPException(
                status_code=503,
                detail="Assistant 未启用，请配置 ASSISTANT_MIRROR_ENABLED=true",
            )
        try:
            from pulse.channels.dingtalk.mirror import mirror_web_message

            mirror_result = mirror_web_message(
                message=message,
                config=config,
                team_id=team.id,
                member_id=user.member.id,
                display_name=user.member.display_name,
                channel_user_id=user.member.dingtalk_user_id,
                actor_role=user.member.portal_role,
            )
        except Exception:
            logger.exception("Assistant web mirror failed")
            raise HTTPException(status_code=502, detail="转发 Assistant 失败")
        return {
            "status": "accepted",
            "session_id": mirror_result.get("session_id"),
            "poll_after": 0,
            "reply": "已记录，小脉处理中，请稍候。",
            "actions": [],
        }

    @app.get("/api/chat/messages")
    def chat_messages(
        after: int = Query(0, ge=0),
        session: Session = Depends(get_db),
        user: PortalUser = Depends(_require_user),
    ):
        team, _repo = _team_repo(session)
        from pulse.web.portal_chat import delivery_to_json, list_portal_chat_deliveries

        rows = list_portal_chat_deliveries(
            session,
            team_id=team.id,
            member_id=user.member.id,
            after_id=after,
        )
        return {"items": [delivery_to_json(row) for row in rows]}

    @app.get("/api/audit-logs", dependencies=[Depends(require_capability("audit:read"))])
    def audit_logs(session: Session = Depends(get_db), limit: int = Query(100, le=500)):
        team, _ = _team_repo(session)
        portal_logs = list_admin_audit_logs(session, team.id, limit=limit)
        alert_rows = session.scalars(
            select(AlertLog)
            .where(AlertLog.team_id == team.id)
            .order_by(AlertLog.created_at.desc())
            .limit(limit)
        ).all()
        return {
            "admin_actions": portal_logs,
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

    @app.get("/api/dashboard/overview", dependencies=[Depends(require_capability("settings:read"))])
    def dashboard_overview(
        session: Session = Depends(get_db),
        user=Depends(require_capability("settings:read")),
    ):
        team, repo = _team_repo(session)
        return build_dashboard_overview(
            config, session, team.id, repo=repo, actor=user.member
        )

    @app.get("/api/system/schedule", dependencies=[Depends(require_capability("settings:read"))])
    def system_schedule(session: Session = Depends(get_db)):
        team, _ = _team_repo(session)
        return build_schedule_plan(config, session, team.id)

    @app.get("/api/system/integrations", dependencies=[Depends(require_capability("settings:read"))])
    def system_integrations(session: Session = Depends(get_db)):
        team, _ = _team_repo(session)
        return build_integrations_status(config, session, team.id)

    register_portal_auth_routes(app, config, get_db, _team_repo)
    register_settings_routes(app, config, get_db, require_capability, _team_repo)
    register_pricing_routes(app, get_db, require_capability, _team_repo)
    register_portal_users_routes(app, config, get_db, require_capability, _team_repo)
    register_accounts_v2_routes(app, get_db, require_capability, _team_repo)
    register_credentials_routes(
        app, get_db, require_capability, _team_repo, config, require_user=_require_user
    )
    register_ingestion_status_routes(app, get_db, require_capability, _team_repo)
    register_knowledge_routes(app, get_db, require_capability, _team_repo, config)
    register_usage_routes(app, get_db, require_capability, _team_repo, config)
    register_quota_routes(app, get_db, require_capability, _team_repo, config)
    register_internal_capabilities_routes(app, get_db, config)
    register_internal_channel_routes(app, config, get_db, _team_repo)
    register_internal_proxy_routes(app, get_db, config)
    register_proxy_keys_routes(app, get_db, require_capability, config)
    register_assistant_capabilities_routes(
        app, get_db, require_capability, _team_repo, config
    )
    register_assistant_sessions_routes(
        app, get_db, require_capability, _team_repo, config
    )
    register_assistant_skills_routes(
        app, get_db, require_capability, _team_repo, config
    )
    register_assistant_prompts_routes(
        app, get_db, require_capability, _team_repo, config
    )

    if admin_spa_dir is not None:
        _mount_admin_static(app, admin_spa_dir)

    enc = (config.credentials.encryption_key or "").strip()
    if enc:
        from pulse.ingestion.credentials import backfill_credential_key_hashes

        session = session_factory()
        try:
            n = backfill_credential_key_hashes(session, enc)
            if n:
                logger.info("Backfilled key_hash on %d credential(s)", n)
        except Exception:
            logger.exception("Failed to backfill credential key_hash")
        finally:
            session.close()

    return app


def resolve_admin_static_dir() -> Path | None:
    """Locate Vue admin build next to this module (packaged with pulse).

    Canonical location: ``pulse/web/static/`` (vite ``outDir`` / Docker COPY).
    Optional override: ``PULSE_ADMIN_STATIC_DIR`` (dev/special deploys only).
    When the override is set, it is the only candidate (no silent fallback).
    """
    override = (os.environ.get("PULSE_ADMIN_STATIC_DIR") or "").strip()
    if override:
        static_dir = Path(override)
        if static_dir.is_dir() and (static_dir / "index.html").exists():
            return static_dir.resolve()
        return None
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.is_dir() and (static_dir / "index.html").exists():
        return static_dir.resolve()
    return None


def _mount_admin_static(app: FastAPI, static_dir: Path | None = None) -> None:
    """Serve Vue SPA under /admin with deep-link fallback to index.html."""
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    resolved = static_dir or resolve_admin_static_dir()
    if resolved is None:
        logger.warning("Vue admin SPA not found; /admin will be unavailable")
        return

    assets_dir = resolved / "assets"
    if assets_dir.is_dir():
        app.mount(
            "/admin/assets",
            StaticFiles(directory=str(assets_dir)),
            name="admin_assets",
        )

    index_file = resolved / "index.html"

    @app.get("/admin")
    @app.get("/admin/")
    @app.get("/admin/{full_path:path}")
    async def admin_spa(full_path: str = "") -> FileResponse:
        # Prefer real files (favicon, etc.); otherwise SPA shell.
        if full_path and ".." not in full_path.split("/"):
            candidate = resolved / full_path
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index_file)

    logger.info("Mounted Vue admin SPA from %s at /admin/", resolved)


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
      const accounts = data.accounts || [];
      const rows = accounts.map(a =>
        `<tr><td>${a.account_identifier || a.account_id}</td><td>${a.ingestion_state || ''}</td><td>${a.primary_member_name || '—'}</td></tr>`
      ).join('');
      document.getElementById('status').innerHTML =
        `<p>账号 ${accounts.length} 个 · 已同步 ${data.submitted_count ?? '—'}/${data.active_count ?? '—'}</p>
         <table><tr><th>账号</th><th>状态</th><th>主使用人</th></tr>${rows}</table>`;
    }
    async function loadAll() {
      try {
        const cfg = await api('/api/config/summary');
        const periodInput = document.getElementById('period');
        if (!periodInput.value) periodInput.value = cfg.current_period;
        const period = periodInput.value;
        const [status, metrics, queries] = await Promise.all([
          api('/api/v2/ingestion-status?period=' + encodeURIComponent(period)),
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
