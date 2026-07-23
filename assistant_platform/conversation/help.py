"""Build bot help text from SkillRegistry (cards + docs)."""

from __future__ import annotations

import re
from typing import Iterable, Literal

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.skills.help_render import (
    build_help_message as _build_help_message,
    build_help_message_from_keys as _build_help_message_from_keys,
    resolve_help_topic,
)

HelpMode = Literal["summary", "detail", "none"]

_DETAIL_PREFIX_RE = re.compile(
    r"^(?:帮助详情|详细说明|命令说明)\s+(.+)$",
    re.IGNORECASE,
)
_HELP_TOPIC_RE = re.compile(r"^帮助\s+(.+)$", re.IGNORECASE)
_TOPIC_HELP_SUFFIX_RE = re.compile(
    r"^(.+?)\s+(?:帮助|怎么用|怎么使用|如何使用|使用说明)$",
    re.IGNORECASE,
)
_GENERAL_HELP_EXACT = frozenset({"帮助", "/help", "help", "命令列表", "可用命令"})
_GENERAL_HELP_PHRASES = (
    "有什么功能",
    "哪些功能",
    "什么功能",
    "有啥功能",
    "有哪些功能",
    "能提供什么帮助",
    "有什么帮助",
    "需要什么帮助",
    "能帮我什么",
    "可以帮我什么",
    "你能做什么",
    "你会做什么",
    "会做什么",
    "怎么用",
    "如何使用",
    "怎么使用",
    "使用说明",
)


def parse_help_request(text: str) -> tuple[HelpMode, str | None]:
    """Return (mode, topic_id). mode=none when not a help request."""
    stripped = (text or "").strip()
    if not stripped:
        return "none", None

    lowered = stripped.lower()
    if lowered in _GENERAL_HELP_EXACT:
        return "summary", None

    m = _DETAIL_PREFIX_RE.match(stripped) or _HELP_TOPIC_RE.match(stripped)
    if m:
        query = m.group(1).strip()
        return "detail", resolve_help_topic(query) or query

    m = _TOPIC_HELP_SUFFIX_RE.match(stripped)
    if m:
        query = m.group(1).strip()
        return "detail", resolve_help_topic(query) or query

    compact = stripped.replace(" ", "")
    if any(phrase in compact for phrase in _GENERAL_HELP_PHRASES):
        if not _TOPIC_HELP_SUFFIX_RE.match(stripped):
            return "summary", None

    return "none", None


def is_help_request(text: str) -> bool:
    mode, _ = parse_help_request(text)
    return mode != "none"


def build_help_message_from_keys(
    granted_keys: Iterable[str],
    *,
    topic: str | None = None,
    member_id: str = "",
    role: str | None = None,
) -> str:
    return _build_help_message_from_keys(
        granted_keys,
        topic=topic,
        member_id=member_id,
        role=role,
    )


def build_help_message(
    capabilities: Iterable[ResolvedCapability],
    *,
    topic: str | None = None,
    member_id: str = "",
    role: str | None = None,
) -> str:
    return _build_help_message(
        capabilities,
        topic=topic,
        member_id=member_id,
        role=role,
    )


def build_help_detail_from_keys(
    topic: str,
    granted_keys: Iterable[str],
    *,
    member_id: str = "",
    role: str | None = None,
) -> str:
    return _build_help_message_from_keys(
        granted_keys,
        topic=topic,
        member_id=member_id,
        role=role,
    )


def build_help_detail(
    topic: str,
    capabilities: Iterable[ResolvedCapability],
    *,
    member_id: str = "",
    role: str | None = None,
) -> str:
    return _build_help_message(
        capabilities,
        topic=topic,
        member_id=member_id,
        role=role,
    )
