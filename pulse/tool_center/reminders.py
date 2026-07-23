from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from pulse.storage.models import AiAccount, AiAccountCredential, Member
from pulse.tool_center.repository import ToolCenterRepository


@dataclass
class NudgeTarget:
    kind: str
    account: AiAccount
    member: Member | None = None


def _credential_map(session, account_ids: list[str]) -> dict[str, AiAccountCredential]:
    if not account_ids:
        return {}
    rows = session.scalars(
        select(AiAccountCredential).where(AiAccountCredential.account_id.in_(account_ids))
    ).all()
    return {row.account_id: row for row in rows}


def build_daily_nudge_targets(
    tool_repo: ToolCenterRepository,
    period: str,
) -> list[NudgeTarget]:
    """解析每日催办目标：无主使用人 → 管理员；Cursor 检查凭证；其他厂商检查手工提交。"""
    targets: list[NudgeTarget] = []
    active = tool_repo.list_active_accounts()
    credentials = _credential_map(tool_repo.session, [a.id for a in active])
    submitted = tool_repo.get_submitted_account_ids(period)

    for account in active:
        if not account.primary_member_id:
            targets.append(NudgeTarget(kind="admin_no_primary", account=account))
            continue

        member = tool_repo.session.get(Member, account.primary_member_id)
        vendor_slug = account.vendor.slug if account.vendor else ""

        if vendor_slug == "cursor":
            cred = credentials.get(account.id)
            if not cred or cred.status != "active":
                targets.append(NudgeTarget(kind="no_credential", account=account, member=member))
            elif cred.last_sync_status == "failed":
                targets.append(NudgeTarget(kind="sync_failed", account=account, member=member))
            continue

        if account.id not in submitted:
            targets.append(NudgeTarget(kind="primary_member", account=account, member=member))

    return targets


def format_deadline_group_message(
    *,
    period: str,
    total_accounts: int,
    submitted_count: int,
    missing_primary_count: int,
) -> str:
    pending = total_accounts - submitted_count
    lines = [
        f"⏰ {period} AI 工具用量提交截止提醒",
        "",
        f"账号上报进度：{submitted_count}/{total_accounts}",
        f"待上报账号：{pending} 个",
    ]
    if missing_primary_count:
        lines.append(f"其中 {missing_primary_count} 个账号尚未指定主使用人（请联系管理员）")
    lines.extend(
        [
            "",
            "Cursor 账号请绑定 API Key；其他工具请私聊机器人提交用量。",
            "（群内不公示个人明细）",
        ]
    )
    return "\n".join(lines)
