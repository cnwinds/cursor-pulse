from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pulse.storage.models import AiAccount, Member

if TYPE_CHECKING:
    from pulse.config import AppConfig

CURSOR_VENDOR_SLUG = "cursor"

_PROXY_MEMBER_PATTERN = re.compile(
    r"(?:这)?(?:个)?是?(?:帮|代)\s*(.+?)\s*(?:提交|上报)?(?:的)?\s*$",
    re.IGNORECASE,
)


def filter_cursor_accounts(accounts: list[AiAccount]) -> list[AiAccount]:
    return sorted(
        [a for a in accounts if a.vendor and a.vendor.slug == CURSOR_VENDOR_SLUG],
        key=lambda a: a.account_identifier.lower(),
    )


def needs_cursor_account_selection(accounts: list[AiAccount]) -> bool:
    return len(filter_cursor_accounts(accounts)) > 1


def format_cursor_account_choice_prompt(
    accounts: list[AiAccount],
    *,
    admin_hint: bool = False,
    subject_name: str | None = None,
) -> str:
    cursor_accounts = filter_cursor_accounts(accounts)
    if subject_name:
        header = f"{subject_name} 有多个 Cursor 账号，请指定这份用量属于哪个账号：\n"
    else:
        header = "您有多个 Cursor 账号，请先指定这份用量属于哪个账号：\n"
    lines = [header]
    for index, account in enumerate(cursor_accounts, start=1):
        plan_name = account.plan.plan_name if account.plan else "未知套餐"
        lines.append(f"{index}. {account.account_identifier}（{plan_name}）")
    lines.append(
        "\n请回复序号（如 1）或账号标识（如 "
        f"{cursor_accounts[0].account_identifier}）。\n"
        "回复「取消」可放弃本次提交。"
    )
    if admin_hint and not subject_name:
        lines.append(
            "\n管理员也可回复「帮 姓名 提交」（如：帮 朱涛 提交），"
            "或直接回复他人的账号邮箱。"
        )
    return "\n".join(lines)


def can_proxy_submit_for_others(config: AppConfig, member: Member) -> bool:
    if member.portal_role in ("owner", "operator"):
        return True
    admin_ids = set(config.admin.dingtalk_user_ids or [])
    if not admin_ids:
        return True
    return member.dingtalk_user_id in admin_ids


def parse_proxy_member_name(text: str) -> str | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match = _PROXY_MEMBER_PATTERN.search(raw)
    if not match:
        return None
    name = match.group(1).strip(" ，,。！!？?的")
    return name or None


def resolve_member_by_display_name(members: list[Member], name: str) -> Member | None:
    needle = (name or "").strip()
    if not needle:
        return None
    exact = [m for m in members if m.display_name == needle]
    if len(exact) == 1:
        return exact[0]
    partial = [m for m in members if needle in m.display_name]
    if len(partial) == 1:
        return partial[0]
    return None


def find_cursor_account_in_pool(text: str, accounts: list[AiAccount]) -> AiAccount | None:
    return parse_account_selection_text(text, filter_cursor_accounts(accounts))


def parse_account_selection_text(
    text: str,
    candidates: list[AiAccount],
) -> AiAccount | None:
    """解析用户对账号的选择；无法识别时返回 None。"""
    raw = (text or "").strip()
    if not raw:
        return None

    body = raw
    for prefix in ("账号", "账户", "account"):
        if body.lower().startswith(prefix):
            body = body[len(prefix) :].strip()
            break

    if body.isdigit():
        index = int(body)
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
        return None

    needle = body.lower()
    exact = [a for a in candidates if a.account_identifier.lower() == needle]
    if len(exact) == 1:
        return exact[0]

    partial = [a for a in candidates if needle in a.account_identifier.lower()]
    if len(partial) == 1:
        return partial[0]

    if "@" in needle:
        local = needle.split("@", 1)[0]
        local_matches = [
            a
            for a in candidates
            if a.account_identifier.lower().split("@", 1)[0] == local
        ]
        if len(local_matches) == 1:
            return local_matches[0]

    return None


def looks_like_account_selection_cancel(text: str) -> bool:
    t = (text or "").strip().lower()
    return t in {"取消", "放弃", "cancel", "算了"}


def looks_like_account_selection_reply(text: str) -> bool:
    t = (text or "").strip()
    if not t or looks_like_account_selection_cancel(t):
        return True
    if t.isdigit():
        return True
    if t.startswith(("账号", "账户")) or "@" in t:
        return True
    return bool(re.match(r"^[\w.+-]+@[\w.-]+\.\w+$", t, flags=re.IGNORECASE))
