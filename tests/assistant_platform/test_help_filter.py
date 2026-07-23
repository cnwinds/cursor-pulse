from __future__ import annotations

from unittest.mock import MagicMock, patch

from assistant_platform.capabilities.seed import seed_phase1_capabilities
from assistant_platform.conversation.help import (
    build_help_detail,
    build_help_message,
    parse_help_request,
)
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.capabilities.resolve import resolve_capabilities
from assistant_platform.config import AssistantConfig
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow

TEAM_ID = "team-help-filter"


def _config() -> AssistantConfig:
    return AssistantConfig(team_id=TEAM_ID, memory_enabled=False)


def _incoming(*, member_id: str, role: str) -> IncomingEventRow:
    return IncomingEventRow(
        event_id="e-help",
        channel="dingtalk",
        channel_message_id="m-help",
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id=member_id,
        sender_display_name="Member",
        conversation_type="private",
        conversation_id=member_id,
        reply_endpoint_json={"member_id": member_id, "role": role},
        text_redacted="帮助",
    )


def test_build_help_message_excludes_admin_commands_for_self_service():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    seed_phase1_capabilities(session, TEAM_ID)
    session.commit()

    caps = resolve_capabilities(
        session, team_id=TEAM_ID, role=None, member_id="member-self"
    )
    text = build_help_message(caps)

    assert text.startswith("## 可用技能")
    assert "| 我的 Cursor |" in text
    assert "| 临时 Key 借用 |" in text
    assert "| 团队运营管理 |" not in text
    assert "帮助 <技能名>" in text
    session.close()


def test_build_help_message_includes_owner_commands():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    seed_phase1_capabilities(session, TEAM_ID)
    session.commit()

    caps = resolve_capabilities(
        session, team_id=TEAM_ID, role="owner", member_id="owner-1"
    )
    text = build_help_message(caps)

    assert "| 团队运营管理 |" in text
    assert "帮助 <技能名>" in text
    session.close()


def test_build_help_detail_for_granted_command():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    seed_phase1_capabilities(session, TEAM_ID)
    session.commit()

    caps = resolve_capabilities(
        session, team_id=TEAM_ID, role=None, member_id="member-self"
    )
    text = build_help_detail("绑定", caps)

    assert text.startswith("## 绑定 Key")
    assert "crsr_" in text
    assert "私聊" in text
    session.close()


def test_build_help_detail_denies_admin_command_for_member():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    seed_phase1_capabilities(session, TEAM_ID)
    session.commit()

    caps = resolve_capabilities(
        session, team_id=TEAM_ID, role=None, member_id="member-self"
    )
    text = build_help_detail("报告", caps)

    assert "暂无权限" in text
    session.close()


def test_parse_help_request_summary_and_detail():
    assert parse_help_request("帮助") == ("summary", None)
    assert parse_help_request("帮助 绑定")[0] == "detail"
    assert parse_help_request("帮助 绑定")[1] == "bind"
    assert parse_help_request("绑定 怎么用")[0] == "detail"
    assert parse_help_request("绑定 怎么用")[1] == "bind"
    assert parse_help_request("查询 谁用得最多") == ("none", None)


def test_generate_reply_help_uses_resolved_capabilities():
    from assistant_platform.config import AssistantLlmConfig

    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    seed_phase1_capabilities(session, TEAM_ID)
    session.commit()

    caps = resolve_capabilities(
        session, team_id=TEAM_ID, role=None, member_id="member-self"
    )
    help_text = build_help_message(caps)

    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    fake_llm = MagicMock()
    fake_llm.complete_with_tools.return_value = {
        "content": help_text,
        "tool_calls": [],
        "raw_assistant_message": {"role": "assistant", "content": help_text},
    }

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        reply = generate_reply_text(
            session,
            config=config,
            incoming=_incoming(member_id="member-self", role="ai_member"),
            text="帮助",
            session_row=ChatSessionRow(
                assistant_id="xiaomai",
                team_id=TEAM_ID,
                channel="dingtalk",
                conversation_type="private",
                conversation_id="member-self",
                user_id="member-self",
            ),
        )

    assert reply.startswith("## 可用技能")
    assert "| 我的 Cursor |" in reply
    assert "| 团队运营管理 |" not in reply
    session.close()
