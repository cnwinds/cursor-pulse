from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="system pending removed; confirmation is model-side"
)

from unittest.mock import MagicMock, patch

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.llm_intent import IntentClassification
from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.conversation.pending import get_pending_capability, set_pending_capability
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow

TEAM_ID = "team-pending"


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
        text_redacted="确认",
    )


def _session_row() -> ChatSessionRow:
    return ChatSessionRow(
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="member-1",
        user_id="member-1",
    )


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
def test_confirm_executes_pending_capability(mock_build_client, mock_classify, db_session):
    mock_build_client.return_value = MagicMock()
    mock_classify.side_effect = AssertionError("LLM should not run for confirm")

    session_row = _session_row()
    set_pending_capability(
        session_row,
        capability_key="key.loan.request",
        arguments={"text": "借用临时 Key"},
        display_name="借用临时 Key",
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
        user_message="✅ 已借出 Key",
    )

    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="确认",
        session_row=session_row,
        pulse_client=pulse_client,
    )

    assert reply == "✅ 已借出 Key"
    assert get_pending_capability(session_row) is None
    pulse_client.invoke.assert_called_once()


def test_bare_confirm_without_pending_returns_hint(db_session):
    config = AssistantConfig(team_id=TEAM_ID, memory_enabled=False)
    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="确认",
        session_row=_session_row(),
    )
    assert "没有待确认" in reply


@patch("assistant_platform.conversation.orchestrator.classify_intent")
@patch("assistant_platform.conversation.orchestrator.build_assistant_llm_client")
@patch("assistant_platform.conversation.orchestrator.resolve_capabilities")
def test_read_capability_skips_confirmation_prompt(
    mock_resolve,
    mock_build_client,
    mock_classify,
    db_session,
):
    mock_build_client.return_value = MagicMock()
    mock_resolve.return_value = [
        ResolvedCapability(
            key="usage.query",
            version="1",
            risk_level="read",
            display_name="查询用量",
            description="",
            confirmation_required=False,
        )
    ]
    mock_classify.return_value = IntentClassification(
        decision="capability",
        capability_key="usage.query",
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
        user_message="你的用量如下",
    )

    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=_incoming(),
        text="查下我的用量",
        session_row=_session_row(),
        pulse_client=pulse_client,
    )

    assert reply == "你的用量如下"
    assert get_pending_capability(_session_row()) is None
    assert "确认" not in reply


def test_borrow_key_rule_intent_requires_confirmation(db_session):
    from assistant_platform.capabilities.seed import seed_phase1_capabilities

    seed_phase1_capabilities(db_session, TEAM_ID)
    db_session.commit()

    session_row = _session_row()
    config = AssistantConfig(
        team_id=TEAM_ID,
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=False),
    )
    incoming = IncomingEventRow(
        event_id="e2",
        channel="dingtalk",
        channel_message_id="m2",
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="member-1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="member-1",
        reply_endpoint_json={"member_id": "member-1", "role": "ai_member"},
        text_redacted="借用临时 Key",
    )

    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=incoming,
        text="借用临时 Key",
        session_row=session_row,
    )

    assert "借用临时 Key" in reply
    assert "确认" in reply
    pending = get_pending_capability(session_row)
    assert pending is not None
    assert pending["capability_key"] == "key.loan.request"


def test_quota_rule_intent_skips_confirmation(db_session):
    from assistant_platform.capabilities.seed import seed_phase1_capabilities

    seed_phase1_capabilities(db_session, TEAM_ID)
    db_session.commit()

    config = AssistantConfig(team_id=TEAM_ID, memory_enabled=False)
    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="本月剩余额度充足",
    )
    incoming = IncomingEventRow(
        event_id="e3",
        channel="dingtalk",
        channel_message_id="m3",
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="member-1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="member-1",
        reply_endpoint_json={"member_id": "member-1", "role": "ai_member"},
        text_redacted="额度",
    )

    reply = generate_reply_text(
        db_session,
        config=config,
        incoming=incoming,
        text="额度",
        session_row=_session_row(),
        pulse_client=pulse_client,
    )

    assert "额度" in reply
    assert "确认" not in reply
    assert get_pending_capability(_session_row()) is None
