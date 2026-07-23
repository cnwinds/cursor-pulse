from __future__ import annotations

from pulse.channels.commands_common import dingtalk_member
from pulse.storage.repository import Repository
from pulse.tool_center.key_loan_ops import (
    list_active_loans,
    read_self_loan,
    request_loan,
    return_loan,
    revoke_loan,
)


def _looks_like_borrow_key_command(text: str) -> bool:
    lowered = text.lower().replace(" ", "")
    if lowered.startswith("借key") or text.startswith("借 Key") or text.startswith("借key"):
        return True
    borrow_phrases = (
        "申请key",
        "申请临时key",
        "借临时key",
        "借用临时key",
        "借用临时",
        "要借key",
        "续key",
        "续借key",
    )
    if any(phrase in lowered for phrase in borrow_phrases):
        return True
    return "用量不够" in text and "key" in lowered


def _looks_like_loan_usage_query(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    t = raw.lower().replace(" ", "")
    if "借出" in t:
        return False
    if _looks_like_borrow_key_command(raw):
        return False
    usage_markers = ("用量", "用了多少", "消耗", "花了多少", "usage", "怎么样", "如何")
    if not any(m in raw or m in t for m in usage_markers):
        return False
    loan_markers = (
        "申请的key",
        "申请了的key",
        "借用的key",
        "借入的key",
        "我借的key",
        "我申请的key",
        "借的key",
    )
    if any(m in t for m in loan_markers):
        return True
    return "申请" in raw and "key" in t


def _looks_like_self_loan_read(text: str) -> bool:
    t = text.strip().lower().replace(" ", "")
    if "借出" in t:
        return False
    if _looks_like_loan_usage_query(text):
        return True
    if text.strip() in ("我的借用", "借用状态"):
        return True
    needles = (
        "我借的key",
        "我借的",
        "借入的key",
        "借入的",
        "我借用的",
        "我借用",
        "借用的key",
        "借用的",
    )
    if any(n in t for n in needles):
        return True
    if t in ("借的key", "借的"):
        return True
    return False


def handle_key_loan_commands(
    text: str,
    user_id: str,
    config,
    repo: Repository,
    *,
    is_admin: bool,
    display_name: str | None = None,
) -> str | None:
    member = repo.get_member_by_dingtalk_id(user_id)
    if member is None and not _looks_like_borrow_key_command(text):
        return None

    if _looks_like_self_loan_read(text):
        if not member:
            return "未找到你的成员记录。"
        return read_self_loan(repo, config, member)

    if text in ("归还 Key", "归还借用", "归还key"):
        if not member:
            return "未找到你的成员记录。"
        return return_loan(repo, config, member)

    if _looks_like_borrow_key_command(text):
        borrower = dingtalk_member(repo, user_id, display_name)
        note = text.split(maxsplit=1)[1].strip() if " " in text else None
        return request_loan(repo, config, borrower, note=note)

    if text in ("借用", "借用列表"):
        if not is_admin:
            return "无权限。"
        return list_active_loans(repo, config, team_id=repo.team_id)

    if text.startswith("撤销借用 "):
        if not is_admin:
            return "无权限。"
        prefix = text.split(maxsplit=1)[1].strip()
        return revoke_loan(repo, config, loan_id_prefix=prefix, team_id=repo.team_id)

    return None
