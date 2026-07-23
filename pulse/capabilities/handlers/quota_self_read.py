from __future__ import annotations

from datetime import date
from typing import Any

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult
from pulse.storage.models import AiAccount, Member
from pulse.tool_center.account_pick import filter_cursor_accounts
from pulse.tool_center.burn_rate import analyze_burn_rate
from pulse.tool_center.key_loans import KeyLoanService
from pulse.tool_center.repository import ToolCenterRepository
from pulse.util.datetime_fmt import format_china_datetime_iso, format_data_updated_line

_STATUS_LABELS = {
    "healthy": "额度充足",
    "warning": "额度偏紧",
    "exhausted": "额度已耗尽",
    "unknown": "暂无额度快照",
}


def _encryption_key(config: Any) -> str:
    if config is None:
        return ""
    creds = getattr(config, "credentials", None)
    if creds is None:
        return ""
    return (getattr(creds, "encryption_key", None) or "").strip()


def _account_item(
    account: AiAccount,
    snapshot,
    today: date,
) -> dict[str, Any]:
    base = {
        "account_id": account.id,
        "account_identifier": account.account_identifier,
        "has_snapshot": snapshot is not None,
    }
    if snapshot is None:
        return {**base, "status": "unknown"}
    analysis = analyze_burn_rate(snapshot, today)
    return {
        **base,
        "status": analysis.status,
        "cycle_start": snapshot.cycle_start.isoformat(),
        "cycle_end": snapshot.cycle_end.isoformat(),
        "total_pct": snapshot.total_pct,
        "auto_pct": snapshot.auto_pct,
        "api_pct": snapshot.api_pct,
        "api_limit_usd": analysis.api_limit_usd,
        "remaining_headroom_pct": analysis.remaining_headroom_pct,
        "quota_progress": analysis.quota_progress,
        "days_until_reset": analysis.days_until_reset,
        "exhausts_before_reset": analysis.exhausts_before_reset,
        "captured_at": format_china_datetime_iso(snapshot.captured_at),
    }


def _pct_cell(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _format_account_section(item: dict[str, Any]) -> list[str]:
    ident = item["account_identifier"]
    if not item["has_snapshot"]:
        return [f"**{ident}**", "暂无额度快照", ""]

    status_label = _STATUS_LABELS.get(item["status"], item["status"])
    total_pct = item.get("total_pct")
    headroom = item.get("remaining_headroom_pct")
    lines = [f"**{ident}**"]
    if total_pct is not None and headroom is not None:
        lines.append(f"状态：{status_label} · 已用 {total_pct:.1f}%，剩余 {headroom:.1f}%")
    else:
        prog = (item.get("quota_progress") or 0) * 100
        lines.append(f"状态：{status_label} · 已用约 {prog:.1f}%")

    lines.extend(
        [
            "",
            "| 额度项 | 已用 |",
            "| --- | --- |",
            f"| Total | {_pct_cell(item.get('total_pct'))} |",
            f"| Auto + Composer | {_pct_cell(item.get('auto_pct'))} |",
            f"| API | {_pct_cell(item.get('api_pct'))} |",
        ]
    )
    api_limit = item.get("api_limit_usd")
    if api_limit:
        lines.append(f"套餐含至少 ${api_limit:.0f} API 用量")
    lines.append(format_data_updated_line(item.get("captured_at")))
    lines.append("")
    return lines


def _format_user_message(accounts_data: list[dict[str, Any]]) -> str:
    lines = ["您的 Cursor 额度：", ""]
    for item in accounts_data:
        lines.extend(_format_account_section(item))
    return "\n".join(lines).rstrip()


def handle_quota_self_read(
    session,
    *,
    request: CapabilityInvokeRequest,
    config: Any,
    op: dict[str, Any],
) -> CapabilityInvokeResult:
    member = session.get(Member, request.actor_member_id)
    if member is None or member.team_id != request.team_id:
        return CapabilityInvokeResult(
            status="failed",
            error_code="forbidden",
            user_message="成员不存在或无权访问",
        )

    tool_repo = ToolCenterRepository(session, request.team_id)
    cursor_accounts = filter_cursor_accounts(
        tool_repo.get_primary_accounts_for_member(member.id)
    )
    if not cursor_accounts:
        return CapabilityInvokeResult(
            status="succeeded",
            user_message="",
            result={
                "schema_version": 1,
                "accounts": [],
                "empty_reason": "no_cursor_account",
            },
        )

    loan_svc = KeyLoanService(session, _encryption_key(config))
    today = date.today()
    accounts_data = [
        _account_item(account, loan_svc.latest_snapshot(account.id), today)
        for account in cursor_accounts
    ]
    return CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={"schema_version": 1, "accounts": accounts_data, "empty_reason": None},
    )
