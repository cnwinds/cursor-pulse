from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.orchestrator import process_session_job
from assistant_platform.conversation.responder import simple_reply
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import BackgroundJobRow, IncomingEventRow, OutboxEventRow

TEAM_ID = "team-orchestrator"
_UNAVAILABLE = "助手暂时不可用，请稍后再试。"


def _event(*, msg_id: str = "m-orch", text: str = "你好") -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id=TEAM_ID,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        reply_endpoint={"webhook": "https://example.test/reply"},
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def _assistant_config(*, llm_enabled: bool = False) -> AssistantConfig:
    return AssistantConfig(
        team_id=TEAM_ID,
        assistant_id="xiaomai",
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=llm_enabled, api_key="k", model="m"),
    )


def test_simple_reply_is_non_empty_chinese_helper():
    reply = simple_reply("随便聊聊")
    assert reply
    assert "额度" in reply
    assert "绑定" in reply


def test_process_session_job_writes_assistant_message_and_reply_outbox():
    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    config = _assistant_config()
    event = _event(text="今天天气不错")

    incoming = IncomingEventRow(
        event_id=event.event_id,
        channel=event.channel,
        channel_message_id=event.channel_message_id,
        assistant_id=event.assistant_id,
        team_id=event.team_id,
        sender_channel_user_id=event.sender_channel_user_id,
        sender_display_name=event.sender_display_name,
        conversation_type=event.conversation_type,
        conversation_id=event.conversation_id,
        reply_endpoint_json=event.reply_endpoint,
        text_redacted=event.text_redacted,
        occurred_at=event.occurred_at,
    )
    session.add(incoming)
    session.flush()

    session_row, user_message = attach_user_message(
        session, event, incoming_event_id=incoming.id
    )
    session.commit()

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=None,
    ):
        process_session_job(
            session,
            {
                "incoming_event_id": incoming.id,
                "session_id": session_row.id,
                "message_id": user_message.id,
            },
            config,
        )
    session.commit()

    assistant_messages = list(
        session.scalars(
            select(ChatMessageRow).where(ChatMessageRow.role == "assistant")
        )
    )
    assert len(assistant_messages) == 1
    assert assistant_messages[0].text_redacted == _UNAVAILABLE

    outbox = session.scalar(
        select(OutboxEventRow).where(OutboxEventRow.kind == "reply.send")
    )
    assert outbox is not None
    assert outbox.payload_json["session_id"] == session_row.id
    assert outbox.payload_json["message_id"] == assistant_messages[0].id
    assert outbox.payload_json["reply_endpoint"] == event.reply_endpoint
    assert outbox.payload_json["text"] == assistant_messages[0].text_redacted

    reply_job = session.scalar(
        select(BackgroundJobRow).where(BackgroundJobRow.job_type == "reply.send")
    )
    assert reply_job is not None
    assert reply_job.payload_json["text"] == assistant_messages[0].text_redacted


def test_process_session_job_quota_intent_uses_capability_when_available():
    from assistant_platform.capabilities.resolve import ResolvedCapability

    Session = init_assistant_db("sqlite://", team_id=TEAM_ID)
    session = Session()
    config = _assistant_config(llm_enabled=True)
    event = _event(text="额度")

    incoming = IncomingEventRow(
        event_id=event.event_id,
        channel=event.channel,
        channel_message_id=event.channel_message_id,
        assistant_id=event.assistant_id,
        team_id=event.team_id,
        sender_channel_user_id=event.sender_channel_user_id,
        sender_display_name=event.sender_display_name,
        conversation_type=event.conversation_type,
        conversation_id=event.conversation_id,
        reply_endpoint_json=event.reply_endpoint,
        text_redacted=event.text_redacted,
        occurred_at=event.occurred_at,
    )
    session.add(incoming)
    session.flush()

    session_row, user_message = attach_user_message(
        session, event, incoming_event_id=incoming.id
    )
    session.commit()

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.side_effect = [
        {
            "content": "",
            "tool_calls": [
                {"id": "c1", "name": "quota_self_read", "arguments": "{}"}
            ],
            "raw_assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "quota_self_read", "arguments": "{}"},
                    }
                ],
            },
        },
        {
            "content": "本月剩余额度 50 元",
            "tool_calls": [],
            "raw_assistant_message": {
                "role": "assistant",
                "content": "本月剩余额度 50 元",
            },
        },
    ]

    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="本月剩余额度 50 元",
        result={"quota": {"remaining_cents": 5000}},
    )

    quota_cap = ResolvedCapability(
        key="quota.self.read",
        version="1",
        risk_level="read",
        display_name="查询额度",
        description="查询本人额度",
        confirmation_required=False,
    )

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[quota_cap],
        ):
            process_session_job(
                session,
                {
                    "incoming_event_id": incoming.id,
                    "session_id": session_row.id,
                    "message_id": user_message.id,
                },
                config,
                pulse_client=pulse_client,
            )
    session.commit()

    from tests.assistant_platform.conftest import final_assistant_message

    assistant_message = final_assistant_message(session, session_id=session_row.id)
    assert assistant_message is not None
    assert assistant_message.text_redacted == "本月剩余额度 50 元"
    pulse_client.invoke.assert_called_once()
