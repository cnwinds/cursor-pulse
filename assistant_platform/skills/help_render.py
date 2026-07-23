from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry

_HELP_TOPICS_PATH = Path(__file__).resolve().parent / "help_topics.yaml"


def _load_topic_index() -> tuple[dict[str, tuple[str, str, str]], dict[str, str]]:
    raw = yaml.safe_load(_HELP_TOPICS_PATH.read_text(encoding="utf-8")) or {}
    index: dict[str, tuple[str, str, str]] = {}
    topic_keys: dict[str, str] = {}
    for item in raw.get("topics") or []:
        skill_id = str(item.get("skill_id") or "").strip()
        label = str(item.get("label") or skill_id).strip()
        topic_key = str(item.get("topic_key") or label).strip()
        if not skill_id:
            continue
        keys = {label.lower().replace(" ", ""), topic_key.lower().replace(" ", "")}
        for alias in item.get("aliases") or []:
            keys.add(str(alias).strip().lower().replace(" ", ""))
        for key in keys:
            if key:
                index[key] = (skill_id, label, topic_key)
        topic_keys[topic_key] = skill_id
    return index, topic_keys


_TOPIC_INDEX, _TOPIC_KEY_TO_SKILL = _load_topic_index()


def skill_actor_from_capabilities(
    capabilities: Iterable[ResolvedCapability],
    *,
    member_id: str = "",
    role: str | None = None,
) -> SkillActorContext:
    return SkillActorContext(
        member_id=member_id,
        role=role,
        authorized_capability_keys=frozenset(c.key for c in capabilities),
    )


def resolve_help_skill_topic(query: str) -> tuple[str, str, str] | None:
    needle = (query or "").strip().lower().replace(" ", "")
    if not needle:
        return None
    if needle in _TOPIC_INDEX:
        return _TOPIC_INDEX[needle]
    for key, value in sorted(_TOPIC_INDEX.items(), key=lambda item: -len(item[0])):
        if needle.startswith(key) or key.startswith(needle):
            return value
    return None


def resolve_help_topic(query: str) -> str | None:
    resolved = resolve_help_skill_topic(query)
    if resolved is None:
        return None
    return resolved[2]


def _has_bot_help(granted: set[str]) -> bool:
    return "bot.help" in granted


def _escape_table_cell(text: str) -> str:
    return (text or "").replace("|", "｜").replace("\n", " ")


def build_help_message_from_keys(
    granted_keys: Iterable[str],
    *,
    topic: str | None = None,
    member_id: str = "",
    role: str | None = None,
) -> str:
    granted = set(granted_keys)
    registry = SkillRegistry()
    actor = SkillActorContext(
        member_id=member_id,
        role=role,
        authorized_capability_keys=granted,
    )
    return _render_help(registry, actor, granted, topic=topic)


def build_help_message(
    capabilities: Iterable[ResolvedCapability],
    *,
    topic: str | None = None,
    member_id: str = "",
    role: str | None = None,
) -> str:
    caps = list(capabilities)
    granted = {cap.key for cap in caps}
    actor = skill_actor_from_capabilities(
        caps,
        member_id=member_id,
        role=role,
    )
    registry = SkillRegistry()
    return _render_help(registry, actor, granted, topic=topic)


def _render_help(
    registry: SkillRegistry,
    actor: SkillActorContext,
    granted: set[str],
    *,
    topic: str | None,
) -> str:
    if not _has_bot_help(granted):
        return "暂无可用帮助。"

    if topic:
        return _format_detail(registry, actor, granted, topic)

    cards = registry.list_cards(actor)
    if not cards:
        return (
            "## 可用技能\n\n"
            "当前仅可查看帮助；如需更多功能请联系管理员分配能力。\n\n"
            "> 发送「帮助 <技能名>」查看详细说明。"
        )

    lines = ["## 可用技能", "", "| 技能 | 说明 |", "| :--- | :--- |"]
    for card in cards:
        lines.append(
            f"| {_escape_table_cell(card.name)} | {_escape_table_cell(card.summary)} |"
        )
    lines.extend(
        [
            "",
            "> 发送 **帮助 <技能名>** 查看详细说明，例如：`帮助 绑定`、`帮助 借 Key`、`帮助 额度`。",
            "> Cursor 绑定 Key 后自动同步，无需 CSV。绑定=同步本人用量；借 Key=临时借用他人 Key。",
        ]
    )
    return "\n".join(lines).strip()


def _format_detail(
    registry: SkillRegistry,
    actor: SkillActorContext,
    granted: set[str],
    topic: str,
) -> str:
    resolved = resolve_help_skill_topic(topic)
    if resolved is None:
        cards = registry.list_cards(actor)
        names = "、".join(c.name for c in cards[:12])
        return (
            f"未找到「{topic}」的说明。\n\n"
            f"可尝试：{names}\n\n"
            "发送「帮助」查看全部技能摘要。"
        )

    skill_id, label, _topic_key = resolved
    visible_ids = {card.skill_id for card in registry.list_cards(actor)}
    if skill_id not in visible_ids:
        return f"你暂无权限查看「{label}」的说明。发送「帮助」查看你有权使用的技能。"

    try:
        doc = registry.load_docs(skill_id, section="all", actor=actor, token_budget=8000)
    except ValueError:
        return f"你暂无权限查看「{label}」的说明。发送「帮助」查看你有权使用的技能。"

    if not doc.markdown.strip():
        return f"「{label}」说明暂未收录，请联系管理员。"

    header = f"## {label}\n\n"
    if doc.truncated:
        header += "（内容较长，已截断；可通过对话继续提问。）\n\n"
    return header + doc.markdown.strip()
