from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.report.service import get_latest_snapshot
from pulse.storage.models import AlertLog, ReminderLog
from pulse.web.settings_store import effective_config_dict, settings_for_api


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def build_schedule_plan(config: AppConfig, session: Session, team_id: str) -> dict:
    effective = effective_config_dict(config, session, team_id)
    collection = effective["collection"]
    memory = effective["memory"]

    jobs = [
        {
            "id": "collection_start",
            "name": "收集开始群通知",
            "cron": f"每月 {collection['start_day']} 日 {collection['start_time']}",
            "process": "pulse serve",
        },
        {
            "id": "daily_nudge",
            "name": "每日私聊催未提交",
            "cron": f"每天 {collection['daily_check_time']}（账期内）",
            "process": "pulse serve",
        },
        {
            "id": "deadline_reminder",
            "name": "截止日群 @全员",
            "cron": f"每月 {collection['deadline_day']} 日 {collection['deadline_time']}",
            "process": "pulse serve",
        },
        {
            "id": "monthly_report",
            "name": "月报发群",
            "cron": f"每月 {collection['report_day']} 日 {collection['report_time']}",
            "process": "pulse serve",
        },
    ]
    if memory.get("evolution_enabled"):
        dow = memory.get("evolution_day_of_week", 6)
        jobs.append(
            {
                "id": "memory_evolution",
                "name": "记忆自进化",
                "cron": f"{_WEEKDAYS[dow % 7]} {memory.get('evolution_time', '02:00')}",
                "process": "pulse serve",
            }
        )

    return {
        "timezone": collection["timezone"],
        "current_period": current_period(config),
        "collection_window": {
            "start_day": collection["start_day"],
            "deadline_day": collection["deadline_day"],
        },
        "jobs": jobs,
        "note": "调度任务在 pulse serve 进程中运行；仅 pulse web 时此处为配置预览。",
    }


def build_integrations_status(config: AppConfig, session: Session, team_id: str) -> dict:
    effective = settings_for_api(config, session, team_id)
    storage_url = config.storage.database_url
    db_kind = "postgres" if storage_url.startswith("postgres") else "sqlite"

    return {
        "dingtalk": {
            "app_configured": bool(config.dingtalk.app_key and config.dingtalk.app_secret),
            "robot_code": bool(config.dingtalk.robot_code),
            "group_configured": bool(config.dingtalk.group_open_conversation_id),
            "chat_id": config.dingtalk.chat_id or None,
        },
        "llm": {
            "enabled": effective["llm"]["enabled"],
            "vision_enabled": effective["llm"]["vision_enabled"],
            "model": effective["llm"]["model"],
            "api_key_configured": bool(config.llm.api_key),
        },
        "integrations": {
            "bi_webhook": bool(effective["integrations"]["webhook_url"]),
            "push_on_report": effective["integrations"].get("push_on_report", True),
        },
        "object_storage": {
            "enabled": config.object_storage.enabled,
            "bucket": config.object_storage.bucket or None,
        },
        "cursor_teams": {
            "enabled": config.cursor_teams.enabled,
            "api_configured": bool(config.cursor_teams.admin_api_key),
        },
        "memory": {
            "evolution_enabled": effective["memory"]["evolution_enabled"],
            "embedding_enabled": effective["memory"].get("embedding_enabled", True),
        },
        "persona": {
            "name": effective["persona"]["name"],
            "role": effective["persona"]["role"],
        },
        "database": {"kind": db_kind, "url_hint": storage_url.split("///")[-1][:80]},
        "processes": {
            "bot_scheduler": "pulse serve（钉钉 Stream + APScheduler）",
            "admin_api": "pulse web（本 API）",
            "admin_ui_dev": "web-admin npm run dev :5173",
        },
    }


def build_dashboard_overview(
    config: AppConfig,
    session: Session,
    team_id: str,
    *,
    repo,
) -> dict:
    period = current_period(config)
    effective = settings_for_api(config, session, team_id)

    active = repo.list_active_members()
    submitted_ids = repo.get_submitted_member_ids(period)
    unsubmitted = repo.get_unsubmitted_members(period)

    member_rows = []
    for m in active:
        member_rows.append(
            {
                "display_name": m.display_name,
                "submitted": m.id in submitted_ids,
                "status": m.status,
            }
        )

    metrics_highlights: dict = {}
    member_costs: list[dict] = []
    snap = get_latest_snapshot(session, period, team_id=team_id)
    if snap and snap.metrics_json:
        mj = snap.metrics_json
        metrics_highlights = {
            "total_events": mj.get("total_events", 0),
            "total_tokens": mj.get("total_tokens", 0),
            "total_cost_usd": mj.get("total_cost_usd", 0),
            "member_count": mj.get("member_count", len(active)),
        }
        by_member = mj.get("cost_by_member") or []
        names = mj.get("member_names") or {}
        for row in by_member[:12]:
            mid = row.get("member_id")
            member_costs.append(
                {
                    "display_name": names.get(mid) or (mid[:8] if mid else "?"),
                    "cost_usd": row.get("value", 0),
                    "rank": row.get("rank"),
                }
            )

    alert_rows = session.scalars(
        select(AlertLog)
        .where(AlertLog.team_id == team_id)
        .order_by(AlertLog.created_at.desc())
        .limit(5)
    ).all()

    reminder_rows = session.scalars(
        select(ReminderLog).order_by(ReminderLog.sent_at.desc()).limit(5)
    ).all()

    return {
        "period": period,
        "summary": {
            "current_period": period,
            "team_slug": config.tenant.slug,
            "timezone": effective["collection"]["timezone"],
            "group_configured": bool(config.dingtalk.group_open_conversation_id),
            "llm_report": effective["llm"]["enabled"],
            "alerts_enabled": effective["alerts"]["enabled"],
            "bi_webhook": bool(effective["integrations"]["webhook_url"]),
        },
        "submission": {
            "active_count": len(active),
            "submitted_count": len(submitted_ids),
            "unsubmitted_names": [m.display_name for m in unsubmitted],
            "members": member_rows,
        },
        "metrics_highlights": metrics_highlights,
        "member_costs": member_costs,
        "recent_alerts": [
            {
                "period": r.period,
                "severity": r.severity,
                "message": r.message,
                "created_at": r.created_at.isoformat(),
            }
            for r in alert_rows
        ],
        "recent_reminders": [
            {
                "type": r.type,
                "period": r.period,
                "sent_at": r.sent_at.isoformat(),
            }
            for r in reminder_rows
        ],
    }
