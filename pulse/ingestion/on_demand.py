from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount, Member

logger = logging.getLogger(__name__)

OnDemandStatus = Literal[
    "already_disabled",
    "disabled_now",
    "check_failed",
    "disable_failed",
]


@dataclass(frozen=True)
class OnDemandEnforceResult:
    status: OnDemandStatus
    previous_hard_limit: int | None = None
    error: str | None = None


def enforce_on_demand_disabled(
    client: CursorApiClient,
    token: str,
    *,
    api_key: str | None = None,
) -> OnDemandEnforceResult:
    """Ensure Cursor On-Demand Spending is disabled for this session token."""
    try:
        data = client.get_hard_limit(token, api_key=api_key)
    except Exception as exc:
        logger.warning("GetHardLimit failed: %s", exc)
        return OnDemandEnforceResult(status="check_failed", error=str(exc))

    previous = data.get("hardLimit")
    previous_limit = int(previous) if previous is not None else None

    if data.get("noUsageBasedAllowed") is True:
        return OnDemandEnforceResult(
            status="already_disabled",
            previous_hard_limit=previous_limit,
        )

    try:
        client.set_hard_limit(
            token,
            hard_limit=0,
            no_usage_based_allowed=True,
            api_key=api_key,
        )
    except Exception as exc:
        logger.warning("SetHardLimit failed: %s", exc)
        return OnDemandEnforceResult(
            status="disable_failed",
            previous_hard_limit=previous_limit,
            error=str(exc),
        )

    return OnDemandEnforceResult(
        status="disabled_now",
        previous_hard_limit=previous_limit,
    )


def format_on_demand_admin_alert(
    account: AiAccount, result: OnDemandEnforceResult
) -> str:
    email = (account.account_identifier or "").strip() or "-"
    if result.status == "disabled_now":
        prev = (
            f"${result.previous_hard_limit}"
            if result.previous_hard_limit is not None
            else "开启"
        )
        return (
            "⚠️ On-Demand Spending 已自动关闭\n\n"
            f"邮箱：{email}\n"
            f"原状态：On-Demand 开启（月限额 {prev}）\n"
            "已关闭，避免超额扣费。"
        )
    if result.status == "disable_failed":
        return (
            "🔴 On-Demand Spending 关闭失败\n\n"
            f"邮箱：{email}\n"
            f"错误：{result.error or 'unknown'}\n"
            "请尽快到 Cursor Dashboard → Spending 手动设为 Disabled。"
        )
    if result.status == "check_failed":
        return (
            "🔴 On-Demand 检测接口失败（需管理员关注）\n\n"
            f"邮箱：{email}\n"
            f"接口：GetHardLimit\n"
            f"错误：{result.error or 'unknown'}\n\n"
            "可能原因：Cursor 非官方 API 变更、鉴权失败或网络异常。\n"
            "请尽快到 Dashboard → Spending 确认 On-Demand 为 Disabled，"
            "并检查 cursor-pulse 的 HardLimit 对接是否仍有效。"
        )
    return (
        f"On-Demand 检查异常 · {email}\n"
        f"状态：{result.status}\n"
        f"错误：{result.error or '-'}"
    )


def resolve_admin_dingtalk_ids(config: AppConfig) -> list[str]:
    """Platform admins from config (DingTalk user ids), deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    for uid in config.admin.dingtalk_user_ids or []:
        value = (uid or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def admin_fallback_member_ids(session: Session, config: AppConfig) -> list[str]:
    admin_dt = {
        uid.strip() for uid in (config.admin.dingtalk_user_ids or []) if uid and uid.strip()
    }
    if not admin_dt:
        return []
    members = session.scalars(
        select(Member).where(Member.status == "active", Member.dingtalk_user_id.in_(admin_dt))
    ).all()
    return [m.id for m in members if m.dingtalk_user_id]


def resolve_on_demand_notify_dingtalk_ids(
    session: Session,
    config: AppConfig,
    account: AiAccount,
) -> list[str]:
    """Resolve unique DingTalk user ids to notify for an On-Demand enforce event."""
    sync_cfg = config.cursor_sync
    configured = sync_cfg.on_demand_notify_member_ids
    if configured is None:
        member_ids = admin_fallback_member_ids(session, config)
    else:
        member_ids = list(configured)

    id_set: set[str] = {mid for mid in member_ids if mid}
    if sync_cfg.on_demand_notify_primary and account.primary_member_id:
        id_set.add(account.primary_member_id)
    if not id_set:
        return []

    members = session.scalars(select(Member).where(Member.id.in_(id_set))).all()
    by_id = {m.id: m for m in members}
    dingtalk_ids: list[str] = []
    seen: set[str] = set()
    for mid in id_set:
        member = by_id.get(mid)
        if not member:
            logger.warning("on-demand notify: member %s not found", mid)
            continue
        uid = (member.dingtalk_user_id or "").strip()
        if not uid:
            logger.warning(
                "on-demand notify: member %s has no dingtalk_user_id", mid
            )
            continue
        if uid in seen:
            continue
        seen.add(uid)
        dingtalk_ids.append(uid)
    return dingtalk_ids
