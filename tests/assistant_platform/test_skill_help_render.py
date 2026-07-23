from __future__ import annotations

from pathlib import Path

from assistant_platform.skills.help_render import (
    build_help_message_from_keys,
    resolve_help_topic,
)
from assistant_platform.skills.models import SkillActorContext
from assistant_platform.skills.registry import SkillRegistry


def test_resolve_help_topic_bind():
    assert resolve_help_topic("绑定") == "bind"
    assert resolve_help_topic("借 Key") == "borrow"


def test_build_help_summary_lists_skills():
    keys = {
        "bot.help",
        "quota.self.read",
        "cursor.key.bind",
        "key.loan.request",
    }
    text = build_help_message_from_keys(keys)
    assert text.startswith("## 可用技能")
    assert "| 我的 Cursor |" in text
    assert "| 团队运营管理 |" not in text
    assert "帮助 <技能名>" in text


def test_build_help_detail_loads_skill_docs():
    keys = {"bot.help", "cursor.key.bind", "quota.self.read"}
    text = build_help_message_from_keys(keys, topic="bind")
    assert text.startswith("## 绑定 Key")
    assert "crsr_" in text
    assert "cursor_key_bind" in text


def test_build_help_detail_denies_admin_skill_for_member():
    keys = {"bot.help", "quota.self.read"}
    text = build_help_message_from_keys(keys, topic="report")
    assert "暂无权限" in text


def test_phase_b_all_skill_files_have_docs():
    reg = SkillRegistry(root=Path("assistant_platform/skills"))
    member = SkillActorContext("m1", "member", frozenset({"quota.self.read"}))
    admin = SkillActorContext("m1", "owner", frozenset({"usage.aggregate"}))
    for skill_id in (
        "cursor.self/overview",
        "key.loan/overview",
        "usage.other/overview",
        "knowledge.share/overview",
        "web.research/overview",
        "bot.guide/overview",
        "dingtalk.setup/overview",
    ):
        doc = reg.load_docs(skill_id, actor=member)
        assert len(doc.markdown.strip()) > 30, skill_id
    doc = reg.load_docs("team.admin/overview", actor=admin)
    assert len(doc.markdown.strip()) > 30
