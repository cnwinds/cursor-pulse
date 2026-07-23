from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="DingTalk text path now uses AgentRuntime; see test_orchestrator_agent.py"
)

from unittest.mock import MagicMock, patch

from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.llm_intent import IntentClassification
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow

TEAM_ID = "team-orchestrator"


@pytest.fixture
def db_session():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _incoming() -> IncomingEventRow:
    return IncomingEventRow(
        event_id="e1",
        channel="dingtalk",
        channel_message_id="m1",
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="member-1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="member-1",
        reply_endpoint_json={"member_id": "member-1", "role": "ai_member"},
        text_redacted="能告诉我借入key的情况吗",
    )


def test_llm_disabled_falls_through_to_simple_reply(db_session):
    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=False),
    )
    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="随便聊聊",
        session_row=None,
    )
    assert "额度" in reply


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
@patch("assistant_platform.conversation.orchestrator.resolve_capabilities")
def test_llm_classify_invokes_capability(
    mock_resolve,
    mock_build_client,
    mock_classify,
    db_session,
):
    from assistant_platform.capabilities.resolve import ResolvedCapability

    mock_build_client.return_value = MagicMock()
    mock_resolve.return_value = [
        ResolvedCapability(
            key="key.loan.self.read",
            version="1",
            risk_level="read",
            display_name="借用",
            description="",
            confirmation_required=False,
        )
    ]
    mock_classify.return_value = IntentClassification(
        decision="capability",
        capability_key="key.loan.self.read",
        confidence=0.9,
        clarify_question="",
        needs_args=False,
    )
    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="📎 当前借用：无",
    )
    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="能告诉我借入key的情况吗",
        session_row=ChatSessionRow(
            assistant_id="xiaomai",
            team_id=TEAM_ID,
            channel="dingtalk",
            conversation_type="private",
            conversation_id="c1",
        ),
        pulse_client=pulse_client,
    )
    assert "借用" in reply
    mock_classify.assert_called_once()
    pulse_client.invoke.assert_called_once()


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
def test_rules_hit_skips_llm(
    mock_build_client,
    mock_classify,
    db_session,
):
    mock_build_client.return_value = MagicMock()
    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="本月剩余额度 50 元",
    )
    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="额度",
        session_row=None,
        pulse_client=pulse_client,
    )
    assert "额度" in reply
    mock_classify.assert_not_called()


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
@patch("assistant_platform.conversation.orchestrator.resolve_capabilities")
def test_clarify_does_not_call_memory(
    mock_resolve,
    mock_build_client,
    mock_classify,
    db_session,
):
    mock_build_client.return_value = MagicMock()
    mock_resolve.return_value = []
    mock_classify.return_value = IntentClassification(
        decision="clarify",
        capability_key=None,
        confidence=0.4,
        clarify_question="你是想查借入还是申请？",
        needs_args=False,
    )
    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=True,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    with patch(
        "assistant_platform.conversation.orchestrator.try_memory_reply"
    ) as mock_memory:
        reply = generate_reply_text(
            db_session,
            config=config,
            incoming=_incoming(),
            text="key 的事",
            session_row=ChatSessionRow(
                assistant_id="xiaomai",
                team_id=TEAM_ID,
                channel="dingtalk",
                conversation_type="private",
                conversation_id="c1",
                user_id="u1",
            ),
        )
        mock_memory.assert_not_called()
    assert "借入" in reply or "申请" in reply


def test_natural_help_phrase_uses_llm_classification(db_session):
    from assistant_platform.capabilities.seed import seed_phase1_capabilities
    from assistant_platform.conversation.llm_intent import IntentClassification

    seed_phase1_capabilities(db_session, TEAM_ID)
    db_session.commit()

    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    pulse_client = MagicMock()
    with patch("assistant_platform.conversation.orchestrator.classify_intent") as mock_classify:
        mock_classify.return_value = IntentClassification(
            decision="capability",
            capability_key="bot.help",
            confidence=0.9,
            clarify_question="",
            needs_args=False,
        )
        reply = generate_reply_text(
            db_session,
            config=config,
            incoming=_incoming(),
            text="你有什么功能",
            session_row=None,
            pulse_client=pulse_client,
        )
        mock_classify.assert_called_once()
    assert "可用命令" in reply
    assert "额度" in reply
    assert "报告" not in reply
    pulse_client.invoke.assert_not_called()


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
@patch("assistant_platform.conversation.orchestrator.resolve_capabilities")
def test_llm_chat_decision_uses_memory_reply(
    mock_resolve,
    mock_build_client,
    mock_classify,
    db_session,
):
    mock_build_client.return_value = MagicMock()
    mock_resolve.return_value = []
    mock_classify.return_value = IntentClassification(
        decision="chat",
        capability_key=None,
        confidence=0.95,
        clarify_question="",
        needs_args=False,
    )
    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=True,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )
    with patch(
        "assistant_platform.conversation.orchestrator.try_memory_reply",
        return_value="你好熊波，很高兴认识你！",
    ) as mock_memory:
        reply = generate_reply_text(
            db_session,
            config=config,
            incoming=_incoming(),
            text="你好，首次见面",
            session_row=ChatSessionRow(
                assistant_id="xiaomai",
                team_id=TEAM_ID,
                channel="dingtalk",
                conversation_type="private",
                conversation_id="member-1",
                user_id="member-1",
            ),
        )
    assert reply == "你好熊波，很高兴认识你！"
    mock_memory.assert_called_once()
