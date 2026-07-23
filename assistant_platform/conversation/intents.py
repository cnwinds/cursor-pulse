"""DEPRECATED: DingTalk text path now uses AgentRuntime. Kept temporarily for rollback reference.

Map exact / structured commands to Assistant capability keys.

自然语言改写 intentionally 返回 None，交由 orchestrator 的 LLM 意图分类处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pulse.channels.commands import BIND_CURSOR_RE, UNBIND_CURSOR_RE
from pulse.tool_center.manual import looks_like_manual_usage
from assistant_platform.conversation.help import (
    _DETAIL_PREFIX_RE,
    _GENERAL_HELP_EXACT,
    _HELP_TOPIC_RE,
    resolve_help_topic,
)


@dataclass(frozen=True)
class CapabilityIntent:
    capability_key: str
    arguments: dict
    confirmed: bool = True


_PREFIX_COMMANDS: tuple[tuple[re.Pattern[str], str, bool], ...] = (
    (re.compile(r"^/?aggregate(?:\s+.+)?$", re.IGNORECASE), "usage.aggregate", True),
    (re.compile(r"^聚合(?:\s+.+)?$"), "usage.aggregate", True),
    (re.compile(r"^/?report(?:\s+.+)?$", re.IGNORECASE), "report.publish", True),
    (re.compile(r"^报告(?:\s+.+)?$"), "report.publish", True),
    (re.compile(r"^成员(?:\s+.+)?$"), "members.manage", True),
    (re.compile(r"^/?alerts(?:\s+.+)?$", re.IGNORECASE), "alerts.run", True),
    (re.compile(r"^告警(?:\s+.+)?$"), "alerts.run", True),
    (re.compile(r"^/?export(?:\s+.+)?$", re.IGNORECASE), "usage.export", True),
    (re.compile(r"^导出(?:\s+.+)?$"), "usage.export", True),
    (re.compile(r"^撤销借用\s+\S+"), "key.loan.revoke", True),
)

_EXACT_COMMANDS: dict[str, tuple[str, bool]] = {
    "额度": ("quota.self.read", True),
    "我的额度": ("quota.self.read", True),
    "我的": ("submission.self.read", True),
    "/my": ("submission.self.read", True),
    "我的用量": ("usage.self.read", True),
    "状态": ("submission.status.read", True),
    "/status": ("submission.status.read", True),
    "我的借用": ("key.loan.self.read", True),
    "借用状态": ("key.loan.self.read", True),
    "归还 Key": ("key.loan.return", True),
    "归还借用": ("key.loan.return", True),
    "归还key": ("key.loan.return", True),
    "借用": ("key.loan.list", True),
    "借用列表": ("key.loan.list", True),
    "借key": ("key.loan.request", False),
    "借 Key": ("key.loan.request", False),
    "设置引导图": ("guide_image.update", False),
}


def _exact_help_intent(stripped: str) -> CapabilityIntent | None:
    if stripped.lower() in _GENERAL_HELP_EXACT:
        return CapabilityIntent("bot.help", {"text": stripped})

    match = _DETAIL_PREFIX_RE.match(stripped) or _HELP_TOPIC_RE.match(stripped)
    if match:
        query = match.group(1).strip()
        topic = resolve_help_topic(query)
        args: dict = {"text": stripped}
        if topic:
            args["topic"] = topic
        return CapabilityIntent("bot.help", args)
    return None


def match_capability_intent(text: str) -> CapabilityIntent | None:
    """仅匹配精确命令或结构化「动词 + 参数」句式；其余返回 None 走 LLM。"""
    stripped = (text or "").strip()
    if not stripped:
        return None

    help_intent = _exact_help_intent(stripped)
    if help_intent:
        return help_intent

    exact = _EXACT_COMMANDS.get(stripped)
    if exact:
        capability_key, confirmed = exact
        return CapabilityIntent(
            capability_key,
            {"text": stripped},
            confirmed=confirmed,
        )

    bind = BIND_CURSOR_RE.match(stripped)
    if bind:
        args: dict = {"api_key": bind.group("key").strip(), "text": stripped}
        if bind.group("email"):
            args["email"] = bind.group("email")
        return CapabilityIntent("cursor.key.bind", args, confirmed=True)

    if UNBIND_CURSOR_RE.match(stripped):
        return CapabilityIntent("cursor.key.unbind", {"text": stripped})

    if looks_like_manual_usage(stripped):
        return CapabilityIntent(
            "usage.manual.submit",
            {"text": stripped},
            confirmed=True,
        )

    for pattern, capability_key, confirmed in _PREFIX_COMMANDS:
        if pattern.match(stripped):
            return CapabilityIntent(
                capability_key,
                {"text": stripped},
                confirmed=confirmed,
            )

    return None


def is_migrated_command_text(text: str) -> bool:
    """True when takeover should skip legacy tip/command handlers."""
    intent = match_capability_intent(text)
    if intent is None:
        return False
    if intent.capability_key == "guide_image.update":
        return False
    return True
