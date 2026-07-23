from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import select

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.orchestrator import process_session_job
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.conversation.turn_inbox import begin_turn
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import BackgroundJobRow, IncomingEventRow

TEAM = "team-turn-orch"


def _cfg() -> AssistantConfig:
    return AssistantConfig(
        team_id=TEAM,
        assistant_id="xiaomai",
        memory_enabled=False,
        llm=AssistantLlmConfig(enabled=True, api_key="k", model="m"),
    )


def _incoming_row(*, msg_id: str, text: str) -> tuple[IncomingMessageEvent, IncomingEventRow]:
    event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id=TEAM,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        reply_endpoint={"member_id": "m1", "role": "member"},
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )
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
    return event, incoming


def test_process_session_job_injects_inbox_during_tool_rounds():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    config = _cfg()

    event1, incoming1 = _incoming_row(msg_id="m1", text="查询用量")
    db.add(incoming1)
    db.flush()
    session_row, user_msg1 = attach_user_message(db, event1, incoming_event_id=incoming1.id)

    event2, incoming2 = _incoming_row(msg_id="m2", text="查6月份的")
    db.add(incoming2)
    db.flush()
    _, user_msg2 = attach_user_message(db, event2, incoming_event_id=incoming2.id)
    begin_turn(db, session_row, trigger_message_id=user_msg1.id)
    db.commit()

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.side_effect = [
        {
            "content": "",
            "tool_calls": [
                {"id": "c1", "name": "usage_query", "arguments": '{"period":"2026-07"}'}
            ],
            "raw_assistant_message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "usage_query",
                            "arguments": '{"period":"2026-07"}',
                        },
                    }
                ],
            },
        },
        {
            "content": "6月用量结果",
            "tool_calls": [],
            "raw_assistant_message": {"role": "assistant", "content": "6月用量结果"},
        },
    ]

    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="ok",
        result={},
    )

    usage_cap = ResolvedCapability(
        key="usage.query",
        version="1",
        risk_level="read",
        display_name="查询用量",
        description="",
        confirmation_required=False,
    )

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[usage_cap],
        ):
            with patch(
                "assistant_platform.capabilities.executor.resolve_capabilities",
                return_value=[usage_cap],
            ):
                process_session_job(
                    db,
                    {
                        "incoming_event_id": incoming1.id,
                        "session_id": session_row.id,
                        "message_id": user_msg1.id,
                    },
                    config,
                    pulse_client=pulse_client,
                )
    db.commit()

    from tests.assistant_platform.conftest import final_assistant_message

    assistant = final_assistant_message(db, session_id=session_row.id)
    assert assistant is not None
    assert assistant.text_redacted == "6月用量结果"

    second_llm_messages = fake_llm.complete_with_tools.call_args_list[1].kwargs["messages"]
    user_texts = [m["content"] for m in second_llm_messages if m.get("role") == "user"]
    assert "查6月份的" in user_texts

    follow_jobs = list(
        db.scalars(
            select(BackgroundJobRow).where(BackgroundJobRow.job_type == "session.process")
        )
    )
    assert follow_jobs == []

    db.refresh(user_msg2)
    assert user_msg2.handled_at is not None


def test_process_session_job_sends_interim_before_final():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    config = _cfg()

    event1, incoming1 = _incoming_row(msg_id="m1", text="查询用量")
    db.add(incoming1)
    db.flush()
    session_row, user_msg1 = attach_user_message(db, event1, incoming_event_id=incoming1.id)
    begin_turn(db, session_row, trigger_message_id=user_msg1.id)
    db.commit()

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.side_effect = [
        {
            "content": "好的，我先查一下，请稍等",
            "tool_calls": [
                {"id": "c1", "name": "usage_query", "arguments": '{"period":"2026-06"}'}
            ],
            "raw_assistant_message": {
                "role": "assistant",
                "content": "好的，我先查一下，请稍等",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "usage_query",
                            "arguments": '{"period":"2026-06"}',
                        },
                    }
                ],
            },
        },
        {
            "content": "6月用量结果",
            "tool_calls": [],
            "raw_assistant_message": {"role": "assistant", "content": "6月用量结果"},
        },
    ]

    pulse_client = MagicMock()
    from assistant_platform.contracts.provider import CapabilityInvokeResult

    pulse_client.invoke.return_value = CapabilityInvokeResult(
        status="succeeded",
        user_message="ok",
        result={},
    )

    usage_cap = ResolvedCapability(
        key="usage.query",
        version="1",
        risk_level="read",
        display_name="查询用量",
        description="",
        confirmation_required=False,
    )

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[usage_cap],
        ):
            with patch(
                "assistant_platform.capabilities.executor.resolve_capabilities",
                return_value=[usage_cap],
            ):
                with patch(
                    "assistant_platform.conversation.orchestrator.send_channel_reply",
                    return_value={"status": "sent"},
                ) as deliver:
                    process_session_job(
                        db,
                        {
                            "incoming_event_id": incoming1.id,
                            "session_id": session_row.id,
                            "message_id": user_msg1.id,
                        },
                        config,
                        pulse_client=pulse_client,
                    )
    db.commit()

    deliver.assert_called_once()
    assert deliver.call_args[0][0]["kind"] == "interim"
    assert deliver.call_args[0][0]["text"] == "好的，我先查一下，请稍等"

    assistant_rows = [
        row
        for row in db.scalars(
            select(ChatMessageRow)
            .where(ChatMessageRow.role == "assistant")
            .order_by(ChatMessageRow.created_at.asc())
        )
        if not (row.meta_json or {}).get("ledger_only")
    ]
    assert len(assistant_rows) == 2
    assert assistant_rows[0].meta_json["kind"] == "interim"
    assert assistant_rows[0].meta_json.get("delivered_sync") is True
    assert assistant_rows[0].text_redacted == "好的，我先查一下，请稍等"
    assert assistant_rows[1].meta_json["kind"] == "final"
    assert assistant_rows[1].text_redacted == "6月用量结果"

    reply_jobs = list(
        db.scalars(select(BackgroundJobRow).where(BackgroundJobRow.job_type == "reply.send"))
    )
    assert len(reply_jobs) == 1
    assert reply_jobs[0].payload_json["kind"] == "final"
    db.close()


def test_process_session_job_queues_follow_up_when_pending_at_end():
    """Mid-turn message that arrives after the last drain should trigger a follow-up job."""
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    config = _cfg()

    event1, incoming1 = _incoming_row(msg_id="m1", text="查询用量")
    db.add(incoming1)
    db.flush()
    session_row, user_msg1 = attach_user_message(db, event1, incoming_event_id=incoming1.id)

    event2, incoming2 = _incoming_row(msg_id="m2", text="查好了吗")
    db.add(incoming2)
    db.flush()
    _, user_msg2 = attach_user_message(db, event2, incoming_event_id=incoming2.id)
    begin_turn(db, session_row, trigger_message_id=user_msg1.id)
    db.commit()

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.return_value = {
        "content": "结果在这",
        "tool_calls": [],
        "raw_assistant_message": {"role": "assistant", "content": "结果在这"},
    }

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[],
        ):
            with patch(
                "assistant_platform.conversation.turn_inbox.TurnInbox.drain_unconsumed",
                return_value=[],
            ):
                process_session_job(
                    db,
                    {
                        "incoming_event_id": incoming1.id,
                        "session_id": session_row.id,
                        "message_id": user_msg1.id,
                    },
                    config,
                    pulse_client=MagicMock(),
                )
    db.commit()

    follow_jobs = list(
        db.scalars(
            select(BackgroundJobRow)
            .where(BackgroundJobRow.job_type == "session.process")
            .order_by(BackgroundJobRow.created_at.asc())
        )
    )
    assert len(follow_jobs) == 1
    assert follow_jobs[0].payload_json["message_id"] == user_msg2.id
    db.close()

