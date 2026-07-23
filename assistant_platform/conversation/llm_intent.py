"""DEPRECATED: DingTalk text path now uses AgentRuntime. Kept temporarily for rollback reference."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from assistant_platform.capabilities.resolve import ResolvedCapability

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


class LlmCompleter(Protocol):
    def complete(self, *, system: str, user: str, temperature: float = 0.1) -> str: ...


@dataclass(frozen=True)
class IntentClassification:
    decision: str  # capability | chat | clarify
    capability_key: str | None
    confidence: float
    clarify_question: str
    needs_args: bool


def capability_needs_extraction(cap: ResolvedCapability) -> bool:
    schema = cap.input_schema or {}
    required = set(schema.get("required") or [])
    props = set((schema.get("properties") or {}).keys())
    extra_required = required - {"text"}
    extra_props = props - {"text"}
    return bool(extra_required or (extra_props and "text" not in props))


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty llm response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


def _catalog_lines(caps: list[ResolvedCapability]) -> str:
    lines = []
    for cap in caps:
        lines.append(
            f"- {cap.key} ({cap.risk_level}): {cap.display_name} — {cap.description}"
        )
    return "\n".join(lines)


def classify_intent(
    client: LlmCompleter,
    *,
    text: str,
    capabilities: list[ResolvedCapability],
    min_confidence: float,
    recent_turns: list[str] | None = None,
    extra_system: str | None = None,
) -> IntentClassification:
    allowed = {c.key: c for c in capabilities}
    system = (
        "你是小脉的意图分类器。根据用户消息，从下列已授权能力中选择一项，"
        "或判定为闲聊(chat)，或在不确定时澄清(clarify)。"
        "只输出 JSON，不要其它文字。字段："
        "decision, capability_key, confidence, clarify_question, needs_args。"
        "capability_key 必须为下列 key 之一或 null。"
        "禁止编造未列出的能力；澄清时只使用能力清单中的 display_name 与 description。"
        "含「查询」「问」前缀，或询问本人/团队用量、tokens、排名、模型分布、合计等问题的消息，"
        "应优先归类为 usage.query（若用户已授权该能力）。"
    )
    if extra_system:
        system += "\n\n" + extra_system.strip()
    user_parts = [
        "已授权能力：",
        _catalog_lines(capabilities),
        "",
        f"用户消息：{text}",
    ]
    if recent_turns:
        user_parts.extend(["", "最近上下文：", *recent_turns[-2:]])
    raw = client.complete(system=system, user="\n".join(user_parts))
    data = _parse_json_object(raw)
    result = IntentClassification(
        decision=str(data.get("decision") or "chat"),
        capability_key=data.get("capability_key"),
        confidence=float(data.get("confidence") or 0.0),
        clarify_question=str(data.get("clarify_question") or ""),
        needs_args=bool(data.get("needs_args")),
    )
    return normalize_classification(result, allowed=allowed, min_confidence=min_confidence)


def normalize_classification(
    raw: IntentClassification,
    *,
    allowed: dict[str, ResolvedCapability],
    min_confidence: float,
) -> IntentClassification:
    if raw.decision == "capability":
        key = raw.capability_key
        if not key or key not in allowed:
            return IntentClassification(
                decision="clarify",
                capability_key=None,
                confidence=raw.confidence,
                clarify_question=raw.clarify_question or "我没太理解你的需求，能再说具体一点吗？",
                needs_args=False,
            )
        if raw.confidence < min_confidence:
            return IntentClassification(
                decision="clarify",
                capability_key=key,
                confidence=raw.confidence,
                clarify_question=raw.clarify_question or "你是想执行哪项操作？",
                needs_args=False,
            )
        cap = allowed[key]
        needs_args = capability_needs_extraction(cap)
        return IntentClassification(
            decision="capability",
            capability_key=key,
            confidence=raw.confidence,
            clarify_question="",
            needs_args=needs_args,
        )
    if raw.decision == "clarify":
        return IntentClassification(
            decision="clarify",
            capability_key=None,
            confidence=raw.confidence,
            clarify_question=raw.clarify_question or "能再说具体一点吗？",
            needs_args=False,
        )
    return IntentClassification(
        decision="chat",
        capability_key=None,
        confidence=raw.confidence,
        clarify_question="",
        needs_args=False,
    )


def extract_arguments(
    client: LlmCompleter,
    *,
    text: str,
    capability: ResolvedCapability,
) -> dict[str, Any]:
    schema = capability.input_schema or {}
    system = (
        "根据用户消息，为该能力提取 JSON 参数。"
        "只输出 JSON 对象，字段必须来自 schema，禁止多余字段。"
        f"schema={json.dumps(schema, ensure_ascii=False)}"
    )
    raw = client.complete(system=system, user=text)
    data = _parse_json_object(raw)
    if "text" not in data:
        data["text"] = text
    required = set(schema.get("required") or [])
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"missing fields: {', '.join(missing)}")
    allowed_props = set((schema.get("properties") or {}).keys())
    return {k: v for k, v in data.items() if k in allowed_props or k == "text"}


def assist_unrecognized_command(
    client: LlmCompleter,
    *,
    text: str,
    capabilities: list[ResolvedCapability],
    failure_hint: str | None = None,
    extra_system: str | None = None,
) -> str | None:
    """Guide the user toward workable commands when rules or capabilities fail."""
    if not capabilities:
        return None
    system = (
        "你是小脉助手。用户发送了一条消息，但系统未能直接执行对应命令。"
        "请根据下列已授权能力，用简短中文说明用户可能想做什么，并给出 1～3 条"
        "可直接复制发送的命令示例（须来自能力清单，禁止编造未列出的命令）。"
        "若用户问的是团队排名类问题但无管理员权限，请说明限制并建议查本人用量。"
        "不要输出 JSON；语气友好、具体。"
    )
    if extra_system:
        system += "\n\n" + extra_system.strip()
    user_parts = [
        "已授权能力：",
        _catalog_lines(capabilities),
        "",
        f"用户消息：{text}",
    ]
    if failure_hint:
        user_parts.extend(["", f"系统反馈：{failure_hint}"])
    raw = client.complete(system=system, user="\n".join(user_parts), temperature=0.3)
    reply = (raw or "").strip()
    return reply or None
