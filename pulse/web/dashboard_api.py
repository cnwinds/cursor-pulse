from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.periods import current_period
from pulse.storage.models import Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.web.permissions import has_permission
from pulse.web.portal import list_pending_portal_users
from pulse.web.settings_store import effective_config_dict, settings_for_api

logger = logging.getLogger(__name__)


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


def _safe_section(section_name: str, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.exception("dashboard section %s build failed", section_name)
        return None


def _quota_section(session: Session, team_id: str) -> dict:
    from pulse.web.quota_api import build_quota_board_items

    items = build_quota_board_items(session, team_id)
    key_by_status = {
        "exhausted": "exhausted_count",
        "warning": "warning_count",
        "healthy": "healthy_count",
        "unknown": "unknown_count",
    }
    counts = {
        "exhausted_count": 0,
        "warning_count": 0,
        "healthy_count": 0,
        "unknown_count": 0,
    }
    for item in items:
        counts[key_by_status.get(item["status"], "unknown_count")] += 1
    risk_top = [
        {
            "account_id": item["account_id"],
            "account_identifier": item["account_identifier"],
            "primary_member_name": item.get("primary_member_name"),
            "status": item["status"],
            "quota_progress": item.get("quota_progress"),
            "projected_exhaustion_date": item.get("projected_exhaustion_date"),
            "days_until_reset": item.get("days_until_reset"),
        }
        for item in items
        if item["status"] in ("exhausted", "warning")
    ][:5]
    return {**counts, "risk_top": risk_top}


def _usage_section(
    config: AppConfig,
    session: Session,
    team_id: str,
    period: str,
    timezone_name: str,
) -> dict:
    from pulse.tool_center.ingestion_status import period_date_range
    from pulse.tool_center.usage_analytics import build_usage_analytics_overview

    period_start, period_end = period_date_range(period)
    # period 按团队时区计算，end 也必须取团队时区的当天，
    # 否则月初边界（团队已进入新月、服务器仍在上月末）会使 end < period_start 触发降级
    end = min(datetime.now(ZoneInfo(timezone_name)).date(), period_end)
    overview = build_usage_analytics_overview(
        session,
        team_id,
        start=period_start,
        end=end,
        timezone=timezone_name,
        top_n=5,
    )
    kpi = overview["kpi"]
    return {
        "period": period,
        "start": overview["start"],
        "end": overview["end"],
        "tokens_total": kpi["tokens_total"],
        "cost_usd": kpi["cost_usd"],
        "event_count": kpi["event_count"],
        "series_by_day": overview["series_by_day"][-14:],
    }


def _loans_section(session: Session, team_id: str) -> dict:
    from pulse.web.quota_api import count_active_loans

    return {"active_count": count_active_loans(session, team_id)}


def _sync_section(session: Session, team_id: str, period: str, actor: Member) -> dict:
    # 复用全量 ingestion payload 只取 summary：内部逐账号构建状态行（每账号有查询），
    # 明细全部丢弃。当前账号规模可接受；若成为瓶颈，应给 ingestion_status 加 summary-only 轻量入口。
    from pulse.tool_center.ingestion_status import build_ingestion_status_payload

    payload = build_ingestion_status_payload(session, team_id, period, actor)
    s = payload["summary"]
    return {
        "total_accounts": s["total_accounts"],
        "submitted_count": s["submitted_count"],
        "synced": s.get("synced", 0),
        "sync_failed": s.get("sync_failed", 0),
        "sync_stale": s.get("sync_stale", 0),
        "no_credential": s.get("no_credential", 0),
        "missing_primary": s.get("missing_primary", 0),
        "unsubmitted": s.get("unsubmitted", 0),
    }


def _proxy_section(session: Session) -> dict:
    from pulse.proxy import service as proxy_service
    from pulse.storage.models import ProxyKey

    keys = session.scalars(select(ProxyKey).where(ProxyKey.status == "active")).all()
    total_tokens = 0
    total_cents = 0
    for key in keys:
        tokens, cents = proxy_service.total_usage(session, key.id)
        total_tokens += tokens
        total_cents += cents
    return {
        "active_key_count": len(keys),
        "total_tokens": total_tokens,
        "total_cost_usd": round(total_cents / 100.0, 2),
    }


def _integrations_section(config: AppConfig, session: Session, team_id: str) -> dict:
    full = build_integrations_status(config, session, team_id)
    issues = []
    if not full["im_group_configured"]:
        issues.append({"key": "im_group", "label": "IM 工作群未配置"})
    return {
        "bot_platform": full["bot_platform"],
        "im_group_configured": full["im_group_configured"],
        "issues": issues,
    }


def _activity_section(session: Session, team_id: str) -> dict:
    from pulse.web.audit import list_admin_audit_logs

    return {"items": list_admin_audit_logs(session, team_id, limit=10)}


def build_dashboard_sections(
    config: AppConfig,
    session: Session,
    team_id: str,
    *,
    period: str,
    timezone_name: str,
    actor: Member,
) -> dict:
    sections: dict[str, dict | None] = {}
    if has_permission(actor, "accounts:read"):
        sections["quota"] = _safe_section("quota", _quota_section, session, team_id)
        sections["usage"] = _safe_section(
            "usage", _usage_section, config, session, team_id, period, timezone_name
        )
        sections["loans"] = _safe_section("loans", _loans_section, session, team_id)
        sections["sync"] = _safe_section(
            "sync", _sync_section, session, team_id, period, actor
        )
    if has_permission(actor, "proxy:read"):
        sections["proxy"] = _safe_section("proxy", _proxy_section, session)
    if has_permission(actor, "settings:read"):
        sections["integrations"] = _safe_section(
            "integrations", _integrations_section, config, session, team_id
        )
    if has_permission(actor, "audit:read"):
        sections["recent_activity"] = _safe_section(
            "recent_activity", _activity_section, session, team_id
        )
    return sections


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

    payload: dict = {
        "period": period,
        "pending_actions": build_pending_actions(session, team_id, actor) if actor else None,
        "sections": (
            build_dashboard_sections(
                config,
                session,
                team_id,
                period=period,
                timezone_name=effective["collection"]["timezone"],
                actor=actor,
            )
            if actor
            else {}
        ),
    }

    # 旧版顶层字段按数据本身的权限门裁剪，与 sections 保持一致：
    # summary 属 settings:read，账号同步计数属 accounts:read。
    if actor and has_permission(actor, "settings:read"):
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
        payload["summary"] = {
            "current_period": period,
            "team_slug": config.tenant.slug,
            "timezone": effective["collection"]["timezone"],
            "bot_platform": im_status["bot_platform"],
            "im_group_configured": im_status["im_group_configured"],
            "group_configured": im_status["im_group_configured"],
        }

    if actor and has_permission(actor, "accounts:read"):
        tool_repo = ToolCenterRepository(session, team_id)
        active_accounts = tool_repo.list_active_accounts()
        submitted_account_ids = tool_repo.get_submitted_account_ids(period)
        missing_primary = tool_repo.accounts_missing_primary()

        submitted_count = len(submitted_account_ids)
        active_count = len(active_accounts)
        unsubmitted_count = max(0, active_count - submitted_count)

        payload["ingestion"] = {
            "active_count": active_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
            "pending_review_count": 0,
            "missing_primary_count": len(missing_primary),
        }
        payload["submission"] = {
            "active_count": active_count,
            "submitted_count": submitted_count,
            "unsubmitted_count": unsubmitted_count,
        }
        payload["sync_stats"] = {
            "synced": submitted_count,
            "pending": 0,
            "missing": unsubmitted_count,
        }

    return payload
