"""Canonical Prompt Studio fragment content (source of truth for seed + auto-upgrade)."""

from __future__ import annotations

# Release created when upgrading legacy production prompts to Agent + tools wording.
AGENT_TOOLS_RELEASE_NAME = "v2-agent-tools"

# Release with persona-only Studio fragments; business rules live in agent_policy.py.
PERSONA_ONLY_RELEASE_NAME = "v3-persona-only"

# Production releases whose precepts contain any of these are considered legacy (pre-agent).
LEGACY_PRECEPTS_MARKERS: tuple[str, ...] = (
    "命令格式",
    "优先给出可执行步骤",
)

# v2-style precepts that duplicated platform business rules (should not appear in Studio).
BUSINESS_RULE_PRECEPTS_MARKERS: tuple[str, ...] = (
    "戒律：",
    "usage_query",
    "usage_self_read",
    "knowledge_tip_create",
    "标记为高风险的",
    "交互节奏：",
)

CANONICAL_FRAGMENTS = None  # deprecated; use canonical_fragments()


def canonical_fragments() -> list[dict[str, str]]:
    from assistant_platform.prompts.loader import load_prompt_fragments_from_files

    return [{"key": k, "content": v} for k, v in load_prompt_fragments_from_files().items()]


def canonical_fragment_by_key(key: str) -> str | None:
    for stub in canonical_fragments():
        if stub["key"] == key:
            return stub["content"]
    return None


def is_legacy_precepts_content(precepts: str) -> bool:
    text = (precepts or "").strip()
    if not text:
        return False
    return any(marker in text for marker in LEGACY_PRECEPTS_MARKERS)


def is_business_rule_precepts_content(precepts: str) -> bool:
    """True when precepts still carry platform business rules (v2-era duplication)."""
    text = (precepts or "").strip()
    if not text:
        return False
    return any(marker in text for marker in BUSINESS_RULE_PRECEPTS_MARKERS)
