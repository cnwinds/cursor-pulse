from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.report.service import get_latest_snapshot
from pulse.storage.models import AlertLog, Member, ReminderLog, UsageIngestion
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.permissions import has_permission
from pulse.web.portal import list_pending_portal_users
from pulse.web.settings_store import effective_config_dict, settings_for_api


_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _format_interval_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} 分钟"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def build_schedule_plan(config: AppConfig, session: Session, team_id: str) -> dict:
    effective = effective_config_dict(config, session, team_id)
    collection = effective["collection"]
    memory = effective["memory"]

    reminders_enabled = bool(collection.get("reminders_enabled", False))

    jobs = []
    if reminders_enabled:
        jobs.extend(
            [
                {
                    "id": "collection_start",
                    "name": "收集开始群通知",
                    "cron": f"每月 {collection['start_day']} 日 {collection['start_time']}",
                    "process": "pulse channel",
                    "enabled": True,
                },
                {
                    "id": "daily_nudge",
                    "name": "每日私聊催未提交",
                    "cron": f"每天 {collection['daily_check_time']}（账期内）",
                    "process": "pulse channel",
                    "enabled": True,
                },
                {
                    "id": "deadline_reminder",
                    "name": "截止日群 @全员",
                    "cron": f"每月 {collection['deadline_day']} 日 {collection['deadline_time']}",
                    "process": "pulse channel",
                    "enabled": True,
                },
            ]
        )
    cursor_sync = effective.get("cursor_sync", config.cursor_sync.model_dump())
    if collection.get("report_on_first_business_day", True):
        pre_time = cursor_sync.get("pre_publish_start_time", "08:00")
        report_cron = (
            f"每月第一个工作日 {pre_time} 刷新 · {collection['report_time']} 发送"
        )
    else:
        report_cron = f"每月 {collection['report_day']} 日 {collection['report_time']}"
    jobs.append(
        {
            "id": "monthly_report",
            "name": "月报发送",
            "cron": report_cron,
            "process": "pulse channel",
            "enabled": True,
        }
    )
    jobs.append(
        {
            "id": "cursor_sync_tick",
            "name": "Cursor账号同步",
            "cron": (
                f"每 {cursor_sync.get('tick_interval_minutes', 2)} 分钟巡检 · "
                f"账号间隔 {_format_interval_minutes(cursor_sync.get('default_interval_minutes', 1440))}"
            ),
            "process": "pulse channel",
            "enabled": bool(cursor_sync.get("enabled", True)),
        }
    )
    # Memory evolution scheduler disabled pending semantic module migration.
    # if memory.get("evolution_enabled"):
    #     jobs.append(...)

    return {
        "timezone": collection["timezone"],
        "current_period": current_period(config),
        "reminders_enabled": reminders_enabled,
        "collection_window": {
            "start_day": collection["start_day"],
            "deadline_day": collection["deadline_day"],
        },
        "jobs": jobs,
        "note": (
            "调度任务在 pulse channel 进程中运行；仅 pulse web 时此处为配置预览。"
            + ("" if reminders_enabled else " 用量提交催办已关闭。")
        ),
    }


def build_integrations_status(config: AppConfig, session: Session, team_id: str) -> dict:
    effective = settings_for_api(config, session, team_id)
    effective_raw = effective_config_dict(config, session, team_id)
    storage_url = config.storage.database_url
    db_kind = "postgres" if storage_url.startswith("postgres") else "sqlite"

    assistant_llm_data = effective_raw.get("assistant_llm", {})
    chat_memory_data = effective.get("chat_memory", {})
    archive_on = bool(chat_memory_data.get("archive", {}).get("enabled"))
    features = chat_memory_data.get("features", {}) or {}
    memory_active = archive_on or any(
        bool(features.get(flag))
        for flag in (
            "archive_pipeline",
            "auto_recall_per_turn",
            "distill_on_close",
            "profile_compile",
            "backfill",
        )
    )
    if not memory_active:
        memory_active = bool(assistant_llm_data.get("memory_enabled", False))
    assistant_llm = {
        "enabled": bool(assistant_llm_data.get("enabled")),
        "model": assistant_llm_data.get("model") or "（未设置）",
        "api_key_configured": bool(assistant_llm_data.get("api_key")),
        "memory_enabled": memory_active,
    }
    assistant_mirror_enabled = bool(config.assistant_mirror.enabled)
    try:
        from assistant_platform.config import load_assistant_config

        ac = load_assistant_config()
        assistant_mirror_enabled = assistant_mirror_enabled or bool(ac.service_token)
    except Exception:
        pass

    pulse_llm_data = effective_raw.get("llm", {})
    dingtalk_data = effective_raw.get("dingtalk", {})
    return {
        "dingtalk": {
            "app_configured": bool(dingtalk_data.get("app_key") and dingtalk_data.get("app_secret")),
            "robot_code": bool(dingtalk_data.get("robot_code")),
            "group_configured": bool(dingtalk_data.get("group_open_conversation_id")),
            "group_title": dingtalk_data.get("group_title") or "",
        },
        "pulse_llm": {
            "enabled": effective["llm"]["enabled"],
            "vision_enabled": effective["llm"]["vision_enabled"],
            "model": effective["llm"]["model"],
            "api_key_configured": bool(pulse_llm_data.get("api_key")),
        },
        "assistant_llm": assistant_llm,
        "integrations": {
            "bi_webhook": bool(effective["integrations"]["webhook_url"]),
            "push_on_report": effective["integrations"].get("push_on_report", True),
        },
        "memory": {
            "evolution_enabled": effective["memory"]["evolution_enabled"],
        },
        "assistant_mirror": {
            "enabled": assistant_mirror_enabled,
        },
        "chat_memory": {
            "archive_enabled": bool(effective.get("chat_memory", {}).get("archive", {}).get("enabled")),
            "auto_recall": bool(
                effective.get("chat_memory", {}).get("features", {}).get("auto_recall_per_turn")
            ),
        },
        "web_search": {
            "enabled": bool(effective_raw.get("web_search", {}).get("enabled")),
            "api_key_configured": bool(effective_raw.get("web_search", {}).get("api_key")),
        },
        "database": {"kind": db_kind, "url_hint": storage_url.split("///")[-1][:80]},
        "runtime_note": (
            "钉钉集成可在团队设置中配置；修改应用凭证或机器人群后需重启 pulse channel。"
            " 收集窗口、错峰同步账号间隔等团队设置保存后立即参与调度；"
            " 巡检间隔（tick）在 pulse channel 启动时注册，修改后需重启 channel。"
        ),
    }


def build_pending_actions(session: Session, team_id: str, actor: Member) -> dict:
    portal_users: list[dict] = []
    if has_permission(actor, "admin:users"):
        portal_users = [
            {
                "id": member.id,
                "display_name": member.display_name,
                "dingtalk_user_id": member.dingtalk_user_id,
            }
            for member in list_pending_portal_users(session, team_id)
        ]

    return {
        "portal_users": portal_users[:10],
        "total_count": len(portal_users),
        "portal_user_count": len(portal_users),
    }


def build_dashboard_overview(
    config: AppConfig,
    session: Session,
    team_id: str,
    *,
    repo,
    actor: Member | None = None,
) -> dict:
    period = current_period(config)
    effective = settings_for_api(config, session, team_id)

    tool_repo = ToolCenterRepository(session, team_id)
    active_accounts = tool_repo.list_active_accounts()
    submitted_account_ids = tool_repo.get_submitted_account_ids(period)
    unsubmitted_accounts = tool_repo.get_unsubmitted_accounts(period)
    missing_primary = tool_repo.accounts_missing_primary()

    pending_count = len(
        [
            a
            for a in unsubmitted_accounts
            if session.scalar(
                select(UsageIngestion.id).where(
                    UsageIngestion.account_id == a.id,
                    UsageIngestion.billing_period == period,
                    UsageIngestion.status == "pending_review",
                )
            )
        ]
    )
    submitted_count = len(submitted_account_ids) + pending_count
    active_count = len(active_accounts)

    metrics_highlights: dict = {}
    cost_summary: dict = {}
    snap = get_latest_snapshot(session, period, team_id=team_id)
    if snap and snap.metrics_json:
        mj = snap.metrics_json
        metrics_highlights = {
            "total_events": mj.get("total_events", 0),
            "total_tokens": mj.get("total_tokens", 0),
            "total_cost_usd": mj.get("total_cost_usd", 0),
            "member_count": mj.get("member_count", len(active_accounts)),
        }
        by_member = mj.get("cost_by_member") or []
        costs = [float(row.get("value") or 0) for row in by_member if float(row.get("value") or 0) > 0]
        total_cost = float(metrics_highlights.get("total_cost_usd") or 0)
        members_with_cost = len(costs)
        cost_summary = {
            "total_cost_usd": total_cost,
            "members_with_cost": members_with_cost,
            "avg_cost_usd": round(total_cost / members_with_cost, 4) if members_with_cost else 0,
            "max_cost_usd": round(max(costs), 4) if costs else 0,
        }

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
        "ingestion": {
            "active_count": active_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": max(0, active_count - submitted_count),
            "pending_review_count": pending_count,
            "missing_primary_count": len(missing_primary),
        },
        # 兼容旧前端字段名
        "submission": {
            "active_count": active_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": max(0, active_count - submitted_count),
        },
        "metrics_highlights": metrics_highlights,
        "cost_summary": cost_summary,
        "alert_summary": {
            "total": len(alert_rows),
            "critical": sum(1 for r in alert_rows if r.severity == "critical"),
            "warning": sum(1 for r in alert_rows if r.severity != "critical"),
        },
        "recent_reminders": [
            {
                "type": r.type,
                "period": r.period,
                "sent_at": r.sent_at.isoformat(),
            }
            for r in reminder_rows
        ],
        "pending_actions": build_pending_actions(session, team_id, actor) if actor else None,
    }
