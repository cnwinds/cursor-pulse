from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from pulse.storage.models import AiAccount, Member
from pulse.tool_center.repository import ToolCenterRepository
from pulse.tool_center.usage import build_manual_usage_summary, infer_metric_unit_for_plan

if TYPE_CHECKING:
    from pulse.storage.repository import Repository

_MANUAL_PREFIXES = ("上报", "用量")
_VENDOR_ALIASES: dict[str, tuple[str, ...]] = {
    "cursor": ("cursor",),
    "zhipu": ("智谱", "zhipu", "glm", "清言"),
    "minimax": ("minimax", "海螺"),
    "codex": ("codex", "chatgpt", "openai", "gpt"),
}
_UNIT_ALIASES: dict[str, str] = {
    "次": "calls",
    "calls": "calls",
    "call": "calls",
    "mcp": "calls",
    "prompts": "prompts",
    "prompt": "prompts",
    "条": "messages",
    "messages": "messages",
    "message": "messages",
    "msg": "messages",
    "元": "cny",
    "cny": "cny",
    "人民币": "cny",
    "usd": "usd",
    "美元": "usd",
    "tokens": "tokens",
    "token": "tokens",
}


@dataclass(frozen=True)
class ManualUsageCommand:
    vendor_slug: str | None
    metric_value: float
    metric_unit: str | None
    raw_text: str


def looks_like_manual_usage(text: str) -> bool:
    t = (text or "").strip()
    return any(t.startswith(prefix) for prefix in _MANUAL_PREFIXES) and len(t) >= 4


def resolve_vendor_slug(token: str) -> str | None:
    needle = (token or "").strip().lower()
    if not needle:
        return None
    for slug, aliases in _VENDOR_ALIASES.items():
        for alias in aliases:
            if needle == alias.lower():
                return slug
    return None


def _normalize_unit(token: str | None) -> str | None:
    if not token:
        return None
    key = token.strip().lower()
    return _UNIT_ALIASES.get(key, key)


def parse_manual_usage_text(text: str) -> ManualUsageCommand:
    """解析「上报 智谱 85」或「用量 minimax 12000 calls」。"""
    raw = text.strip()
    body = raw
    for prefix in _MANUAL_PREFIXES:
        if body.startswith(prefix):
            body = body[len(prefix) :].strip()
            break

    match = re.match(
        r"^(?P<vendor>\S+)\s+(?P<value>[\d.]+)\s*(?P<unit>\S+)?$",
        body,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            "格式：上报 <工具> <数值> [单位]\n"
            "示例：上报 智谱 85\n"
            "示例：用量 minimax 12000 calls\n"
            "示例：上报 codex 45 messages"
        )

    vendor_slug = resolve_vendor_slug(match.group("vendor"))
    if not vendor_slug:
        raise ValueError(f"未知工具：{match.group('vendor')}")

    value = float(match.group("value"))
    if value < 0:
        raise ValueError("用量数值不能为负")

    unit = _normalize_unit(match.group("unit"))
    return ManualUsageCommand(
        vendor_slug=vendor_slug,
        metric_value=value,
        metric_unit=unit,
        raw_text=raw,
    )


def pick_account_for_vendor(
    accounts: list[AiAccount],
    vendor_slug: str,
) -> AiAccount:
    matched = [a for a in accounts if a.vendor and a.vendor.slug == vendor_slug]
    if not matched:
        raise ValueError(f"未找到 {vendor_slug} 账号台账，请联系管理员添加")
    if len(matched) > 1:
        ids = "、".join(a.account_identifier for a in matched)
        raise ValueError(f"存在多个 {vendor_slug} 账号（{ids}），请联系管理员指定主使用人账号")
    return matched[0]


def pick_account_for_screenshot(accounts: list[AiAccount]) -> AiAccount | None:
    if not accounts:
        return None
    if len(accounts) == 1:
        return accounts[0]
    slugs = {a.vendor.slug for a in accounts if a.vendor}
    if len(slugs) == 1:
        return accounts[0]
    non_cursor = [a for a in accounts if a.vendor and a.vendor.slug != "cursor"]
    if len(non_cursor) == 1:
        return non_cursor[0]
    return None


class ManualUsageService:
    def __init__(self, session: Session, team_id: str):
        self.session = session
        self.team_id = team_id
        self.tool_repo = ToolCenterRepository(session, team_id)

    def submit_for_member(
        self,
        *,
        member: Member,
        period: str,
        command: ManualUsageCommand,
        submit_channel: str,
        account_id: str | None = None,
        repo: Repository,
        upgrade_notify: tuple | None = None,
        raw_source=None,
        raw_files_dir=None,
        extraction_confidence: float = 1.0,
        status: str = "confirmed",
        breakdown_by_model: dict | None = None,
    ) -> tuple:
        account = self._resolve_account(member, command.vendor_slug, account_id)
        plan = self.tool_repo.get_plan(account.plan_id)
        if not plan:
            raise ValueError("账号套餐不存在")

        methods = plan.usage_submit_methods or []
        if "manual" not in methods and "screenshot" not in methods:
            raise ValueError(f"{plan.plan_name} 不支持手工上报，请使用官方导出方式")

        unit = command.metric_unit or infer_metric_unit_for_plan(plan)
        summary = build_manual_usage_summary(
            plan=plan,
            metric_value=command.metric_value,
            metric_unit=unit,
            breakdown_by_model=breakdown_by_model,
        )

        submission = repo.save_manual_submission(
            member=member,
            period=period,
            account_id=account.id,
            summary=summary,
            submit_channel=submit_channel,
            raw_text=command.raw_text,
            raw_source=raw_source,
            raw_files_dir=raw_files_dir,
            extraction_confidence=extraction_confidence,
            status=status,
            upgrade_notify=upgrade_notify,
        )
        return submission, account, summary

    def submit_explicit(
        self,
        *,
        member: Member,
        period: str,
        account_id: str,
        metric_value: float,
        metric_unit: str | None,
        submit_channel: str,
        repo: Repository,
        raw_text: str | None = None,
        breakdown_by_model: dict | None = None,
        upgrade_notify: tuple | None = None,
    ):
        account = self.tool_repo.get_account(account_id)
        if not account:
            raise ValueError("账号不存在")
        plan = self.tool_repo.get_plan(account.plan_id)
        if not plan:
            raise ValueError("账号套餐不存在")

        unit = metric_unit or infer_metric_unit_for_plan(plan)
        summary = build_manual_usage_summary(
            plan=plan,
            metric_value=metric_value,
            metric_unit=unit,
            breakdown_by_model=breakdown_by_model,
        )
        submission = repo.save_manual_submission(
            member=member,
            period=period,
            account_id=account_id,
            summary=summary,
            submit_channel=submit_channel,
            raw_text=raw_text,
            upgrade_notify=upgrade_notify,
        )
        return submission, account, summary

    def _resolve_account(
        self,
        member: Member,
        vendor_slug: str | None,
        account_id: str | None,
    ) -> AiAccount:
        if account_id:
            account = self.tool_repo.get_account(account_id)
            if not account:
                raise ValueError("账号不存在")
            if account.primary_member_id and account.primary_member_id != member.id:
                raise ValueError("仅账号主使用人可提交用量")
            return account

        primary_accounts = self.tool_repo.get_primary_accounts_for_member(member.id)
        if not primary_accounts:
            raise ValueError("您没有可提交的主使用人账号，请联系管理员")

        if vendor_slug:
            return pick_account_for_vendor(primary_accounts, vendor_slug)

        if len(primary_accounts) == 1:
            return primary_accounts[0]

        vendors = sorted(
            {a.vendor.name for a in primary_accounts if a.vendor}
        )
        raise ValueError(
            "您有多个工具账号，请指定工具名。\n"
            f"示例：上报 {vendors[0] if vendors else '智谱'} 85\n"
            f"可选：{'、'.join(vendors)}"
        )
