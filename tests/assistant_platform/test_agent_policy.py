from __future__ import annotations

from pathlib import Path

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_policy import build_agent_system
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry


def _cap(key: str) -> ResolvedCapability:
    return ResolvedCapability(
        key=key,
        version="1",
        risk_level="read",
        display_name=key,
        description=key,
        input_schema={"type": "object", "properties": {}},
    )


def test_private_policy_allows_full_key_and_prompt():
    system = build_agent_system(
        prompt_studio_supplement="## heart.md\n你是小脉",
        capabilities=[_cap("key.loan.self.read")],
        subject_id="u1",
        conversation_type="private",
    )
    assert "私聊" in system
    assert "完整展示" in system
    assert "Prompt Studio" in system
    assert "heart.md" in system
    assert "不得掩码" in system


def test_group_policy_keeps_masking_rules():
    system = build_agent_system(
        prompt_studio_supplement="## heart.md\n你是小脉",
        capabilities=[_cap("key.loan.self.read")],
        subject_id="u1",
        conversation_type="group",
    )
    assert "群聊" in system
    assert "掩码" in system
    assert "不得完整展示" in system


def test_policy_includes_hybrid_web_search_rules():
    system = build_agent_system(
        prompt_studio_supplement="",
        capabilities=[_cap("web.search"), _cap("web.fetch")],
        subject_id="u1",
        conversation_type="private",
    )
    assert "必须调用" in system
    assert "明确禁止联网" in system
    assert "不可信" in system
    assert "私人历史" in system
    assert "旧知识伪装" in system
    assert "web_search" in system or "联网搜索" in system


def test_policy_requires_skill_layout_for_structured_result():
    system = build_agent_system(
        prompt_studio_supplement="",
        capabilities=[_cap("usage.self.read")],
        subject_id="u1",
        conversation_type="private",
    )
    assert "schema_version" in system
    assert "展示版式" in system
    assert "禁止编造" in system
    assert "user_message 为空" in system or "仅失败时" in system


def test_policy_injects_skill_cards_when_enabled():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    actor = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    cards = [c for c in reg.list_cards(actor) if c.skill_id == "cursor.self/tasks/quota"]
    assert cards
    previews = {
        cards[0].skill_id: reg.load_docs(
            cards[0].skill_id, actor=actor, start_line=1, max_lines=200
        )
    }
    system = build_agent_system(
        prompt_studio_supplement="",
        capabilities=[_cap("quota.self.read")],
        subject_id="u1",
        conversation_type="private",
        skill_cards=cards,
        skill_previews=previews,
        skills_enabled=True,
    )
    assert "## 可用技能" in system
    assert "cursor.self/tasks/quota" in system
    assert "skill_preview" in system
    assert "已载入" in system
    assert "展示版式" in system or "quota_self_read" in system
    assert "load_skill_docs" in system
    assert "当前可用能力（display_name）" not in system
