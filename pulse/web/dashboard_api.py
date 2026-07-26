from __future__ import annotations

from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.storage.models import Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.permissions import has_permission
from pulse.web.portal import list_pending_portal_users
from pulse.web.settings_store import effective_config_dict, settings_for_api


def _format_interval_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} 分钟"
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} 小时"
    return f"{minutes} 分钟"


def _period_for_effective(config: AppConfig, effective: dict) -> str:
    """Compute billing period using team-effective timezone / format."""
    from pulse.config import CollectionConfig

    collection = effective.get("collection") or {}
    cfg = config.model_copy(
        update={
            "collection": CollectionConfig(
                timezone=str(collection.get("timezone") or config.collection.timezone),
                period_format=str(
                    collection.get("period_format") or config.collection.period_format
                ),
            )
        }
    )
    return current_period(cfg)


def build_schedule_plan(config: AppConfig, session: Session, team_id: str) -> dict:
    effective = effective_config_dict(config, session, team_id)
    collection = effective["collection"]
    cursor_sync = effective.get("cursor_sync", config.cursor_sync.model_dump())

    jobs = [
        {
            "id": "cursor_sync_tick",
            "name": "Cursor 账号同步",
            "cron": (
                f"每 {cursor_sync.get('tick_interval_minutes', 2)} 分钟巡检 · "
                f"账号间隔 {_format_interval_minutes(cursor_sync.get('default_interval_minutes', 1440))}"
            ),
            "process": "pulse channel",
            "enabled": bool(cursor_sync.get("enabled", True)),
        },
        {
            "id": "expire_key_loans",
            "name": "Key 借用到期回收",
            "cron": "每天 03:00",
            "process": "pulse channel",
            "enabled": True,
        },
    ]

    return {
        "timezone": collection["timezone"],
        "current_period": _period_for_effective(config, effective),
        "jobs": jobs,
        "note": (
            "调度任务在 pulse channel 进程中运行；仅 pulse web 时此处为配置预览。"
            " 巡检间隔（tick）在 pulse channel 启动时注册，修改后需重启 channel。"
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

    from pulse.web.channel_status import resolve_im_group_status

    dingtalk_data = effective_raw.get("dingtalk", {})
    feishu_data = effective_raw.get("feishu", {})
    im_status = resolve_im_group_status(effective_raw)
    return {
        "bot_platform": im_status["bot_platform"],
        "im_group_configured": im_status["im_group_configured"],
        "dingtalk": {
            "app_configured": bool(dingtalk_data.get("app_key") and dingtalk_data.get("app_secret")),
            "robot_code": bool(dingtalk_data.get("robot_code")),
            "group_configured": im_status["dingtalk_group_configured"],
            "group_title": dingtalk_data.get("group_title") or "",
        },
        "feishu": {
            "app_configured": bool(feishu_data.get("app_id") and feishu_data.get("app_secret")),
            "group_configured": im_status["feishu_group_configured"],
            "group_chat_id": feishu_data.get("group_chat_id") or "",
            "bot_open_id": bool(feishu_data.get("bot_open_id")),
        },
        "assistant_llm": assistant_llm,
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
            "消息渠道与钉钉/飞书凭证可在团队设置中配置；修改 bot 平台、应用凭证或工作群后需重启 pulse channel。"
            " 错峰同步账号间隔等团队设置保存后立即参与调度；"
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
                "channel_user_id": member.channel_user_id,
                "channel": getattr(member, "channel", None) or "web",
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
    effective = settings_for_api(config, session, team_id)
    effective_raw = effective_config_dict(config, session, team_id)
    period = _period_for_effective(config, effective_raw)

    tool_repo = ToolCenterRepository(session, team_id)
    active_accounts = tool_repo.list_active_accounts()
    submitted_account_ids = tool_repo.get_submitted_account_ids(period)
    missing_primary = tool_repo.accounts_missing_primary()

    submitted_count = len(submitted_account_ids)
    active_count = len(active_accounts)
    unsubmitted_count = max(0, active_count - submitted_count)

    sync_stats = {
        "synced": submitted_count,
        "pending": 0,
        "missing": unsubmitted_count,
    }

    from pulse.web.channel_status import resolve_im_group_status

    merged_for_im = {
        "bot": {"name": (effective.get("bot") or {}).get("name") or config.bot.name},
        "dingtalk": {
            "group_open_conversation_id": (effective.get("dingtalk") or {}).get(
                "group_open_conversation_id"
            )
            or config.dingtalk.group_open_conversation_id,
        },
        "feishu": {
            "group_chat_id": (effective.get("feishu") or {}).get("group_chat_id")
            or config.feishu.group_chat_id,
        },
    }
    im_status = resolve_im_group_status(merged_for_im)

    return {
        "period": period,
        "summary": {
            "current_period": period,
            "team_slug": config.tenant.slug,
            "timezone": effective["collection"]["timezone"],
            "bot_platform": im_status["bot_platform"],
            "im_group_configured": im_status["im_group_configured"],
            "group_configured": im_status["im_group_configured"],
        },
        "ingestion": {
            "active_count": active_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
            "pending_review_count": 0,
            "missing_primary_count": len(missing_primary),
        },
        "submission": {
            "active_count": active_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
        },
        "sync_stats": sync_stats,
        "pending_actions": build_pending_actions(session, team_id, actor) if actor else None,
    }
