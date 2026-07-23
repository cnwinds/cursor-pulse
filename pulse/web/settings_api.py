from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.web.audit import log_admin_action
from pulse.web.deps import PortalUser
from pulse.web.schemas import SettingsPatchBody
from pulse.web.settings_store import (
    EDITABLE_SECTIONS,
    patch_team_setting,
    reveal_setting_secret,
    settings_for_api,
)


def register_settings_routes(app, config: AppConfig, get_db, require_capability, team_repo_fn):
    @app.get("/api/config/summary", dependencies=[Depends(require_capability("settings:read"))])
    def config_summary(session: Session = Depends(get_db)):
        team, _repo = team_repo_fn(session)
        effective = settings_for_api(config, session, team.id)
        dingtalk = effective.get("dingtalk", {})
        return {
            "current_period": current_period(config),
            "team_slug": config.tenant.slug,
            "timezone": effective["collection"]["timezone"],
            "group_configured": bool(dingtalk.get("group_open_conversation_id")),
            "llm_report": effective["llm"]["enabled"],
            "llm_vision": effective["llm"]["vision_enabled"],
            "alerts_enabled": effective["alerts"]["enabled"],
            "bi_webhook": bool(effective["integrations"]["webhook_url"]),
        }

    @app.get("/api/settings", dependencies=[Depends(require_capability("settings:read"))])
    def get_settings(session: Session = Depends(get_db)):
        team, _repo = team_repo_fn(session)
        return settings_for_api(config, session, team.id)

    @app.get(
        "/api/settings/{section}/reveal/{key}",
        dependencies=[Depends(require_capability("settings:read"))],
    )
    def reveal_settings_secret(
        section: str,
        key: str,
        session: Session = Depends(get_db),
        user: PortalUser = Depends(require_capability("settings:read")),
    ):
        team, _repo = team_repo_fn(session)
        try:
            value = reveal_setting_secret(
                config,
                session,
                team_id=team.id,
                section=section,
                key=key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        log_admin_action(
            session,
            team_id=team.id,
            member_id=user.member.id,
            action="settings.secret_reveal",
            capability="settings:read",
            detail=f"{section}.{key}",
        )
        session.commit()
        return {"value": value}

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
        team, _repo = team_repo_fn(session)
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
            if section == "cursor_sync" and any(
                k in body.data
                for k in (
                    "enabled",
                    "default_interval_minutes",
                    "month_close_interval_minutes",
                    "tick_interval_minutes",
                )
            ):
                from pulse.ingestion.sync_schedule import accelerate_sync_schedules
                from pulse.settings import effective_config

                runtime = effective_config(config, session, team.id)
                accelerate_sync_schedules(session, runtime)
            session.commit()
        except ValueError as exc:
            raise HTTPException(400, detail=str(exc)) from exc
        return settings_for_api(config, session, team.id)
