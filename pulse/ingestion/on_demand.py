from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount

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
    note = (account.shared_note or "").strip()
    label = note or email
    if result.status == "disabled_now":
        prev = (
            f"${result.previous_hard_limit}"
            if result.previous_hard_limit is not None
            else "开启"
        )
        return (
            "⚠️ On-Demand Spending 已自动关闭\n\n"
            f"账号：{label}\n"
            f"邮箱：{email}\n"
            f"原状态：On-Demand 开启（月限额 {prev}）\n"
            "已调用 SetHardLimit 关闭，避免超额扣费。"
        )
    if result.status == "disable_failed":
        return (
            "🔴 On-Demand Spending 关闭失败\n\n"
            f"账号：{label}\n"
            f"邮箱：{email}\n"
            f"错误：{result.error or 'unknown'}\n"
            "请尽快到 Cursor Dashboard → Spending 手动设为 Disabled。"
        )
    return (
        f"On-Demand 检查异常 · {label}\n"
        f"状态：{result.status}\n"
        f"错误：{result.error or '-'}"
    )
