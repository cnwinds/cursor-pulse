from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from assistant_platform.config import (
    AssistantChatMemoryConfig,
    AssistantConfig,
    AssistantLlmConfig,
    MemoryEmbeddingConfig,
)
from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.orchestrator import generate_reply_text
from assistant_platform.conversation.session_store import attach_user_message
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.skills.models import SkillCard
from assistant_platform.storage.db import init_assistant_db
from assistant_platform.storage.models import IncomingEventRow

TEAM = "team-agent-orch"
B_SECRET = "u-b-only-secret-phrase"


def _cfg() -> AssistantConfig:
    return AssistantConfig(
        team_id=TEAM,
        assistant_id="xiaomai",
        memory_enabled=False,
        # Disable embedding so the skill vector index uses the local
        # HashingEmbedder (no network) during tests.
        chat_memory=AssistantChatMemoryConfig(
            embedding=MemoryEmbeddingConfig(enabled=False)
        ),
        llm=AssistantLlmConfig(
            enabled=True, api_key="k", model="m", base_url="https://example.test/v1"
        ),
    )


def test_generate_reply_uses_agent_not_intent_matcher():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id="m1",
        assistant_id="xiaomai",
        team_id=TEAM,
        sender_channel_user_id="u1",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u1",
        reply_endpoint={"member_id": "m1", "role": "member"},
        text_redacted="查下我的额度",
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
    db.add(incoming)
    db.flush()
    session_row, _msg = attach_user_message(db, event, incoming_event_id=incoming.id)
    db.commit()

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.return_value = {
        "content": "Agent 已处理",
        "tool_calls": [],
        "raw_assistant_message": {"role": "assistant", "content": "Agent 已处理"},
    }

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[],
        ):
            reply = generate_reply_text(
                db,
                config=_cfg(),
                incoming=incoming,
                text="查下我的额度",
                session_row=session_row,
            )
    assert reply == "Agent 已处理"
    assert fake_llm.complete_with_tools.called


def test_agent_history_does_not_leak_other_users():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    now = datetime.now(timezone.utc)
    s_a = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u-a",
        user_id="u-a",
        status="open",
        last_activity_at=now,
    )
    s_b = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u-b",
        user_id="u-b",
        status="open",
        last_activity_at=now,
    )
    db.add_all([s_a, s_b])
    db.add_all(
        [
            ChatMessageRow(
                session_id=s_a.id,
                role="user",
                text_redacted="A 的历史问题",
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_a.id,
                role="assistant",
                text_redacted="A 的历史回答",
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_b.id,
                role="user",
                text_redacted=B_SECRET,
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_b.id,
                role="assistant",
                text_redacted="B 的私密回复",
                secret_refs_json=[],
                meta_json={},
            ),
        ]
    )
    db.flush()

    event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id="m-a2",
        assistant_id="xiaomai",
        team_id=TEAM,
        sender_channel_user_id="u-a",
        sender_display_name="Alice",
        conversation_type="private",
        conversation_id="u-a",
        reply_endpoint={"member_id": "m-a", "role": "member"},
        text_redacted="继续帮我查",
        occurred_at=now,
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
    db.add(incoming)
    db.flush()
    session_row, _msg = attach_user_message(db, event, incoming_event_id=incoming.id)
    db.commit()
    assert session_row.id == s_a.id

    fake_llm = MagicMock()
    fake_llm.complete_with_tools.return_value = {
        "content": "仅 A 可见",
        "tool_calls": [],
        "raw_assistant_message": {"role": "assistant", "content": "仅 A 可见"},
    }

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ):
        with patch(
            "assistant_platform.conversation.orchestrator.resolve_capabilities",
            return_value=[],
        ):
            reply = generate_reply_text(
                db,
                config=_cfg(),
                incoming=incoming,
                text="继续帮我查",
                session_row=session_row,
            )

    assert reply == "仅 A 可见"
    assert fake_llm.complete_with_tools.called
    messages = fake_llm.complete_with_tools.call_args.kwargs["messages"]
    serialized = json.dumps(messages, ensure_ascii=False)
    assert B_SECRET not in serialized
    assert "B 的私密回复" not in serialized
    assert "A 的历史问题" in serialized


def _prepare_session(db, text: str):
    event = IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id="m-route",
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
    db.add(incoming)
    db.flush()
    session_row, _msg = attach_user_message(db, event, incoming_event_id=incoming.id)
    db.commit()
    return incoming, session_row


def _fake_llm() -> MagicMock:
    fake_llm = MagicMock()
    fake_llm.complete_with_tools.return_value = {
        "content": "ok",
        "tool_calls": [],
        "raw_assistant_message": {"role": "assistant", "content": "ok"},
    }
    return fake_llm


def test_orchestrator_injects_routed_skill_card():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    incoming, session_row = _prepare_session(db, "查下我的额度")

    fake_llm = _fake_llm()
    card = SkillCard(
        skill_id="cursor.self/tasks/quota",
        name="额度查询技能名片",
        summary="查看本人 Cursor 额度",
        when_to_use=("用户问额度",),
        audience=frozenset({"member"}),
    )

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ), patch(
        "assistant_platform.conversation.orchestrator.resolve_capabilities",
        return_value=[],
    ), patch(
        "assistant_platform.skills.vector_index.SkillVectorIndex.route_cards",
        return_value=[card],
    ):
        generate_reply_text(
            db,
            config=_cfg(),
            incoming=incoming,
            text="查下我的额度",
            session_row=session_row,
        )

    messages = fake_llm.complete_with_tools.call_args.kwargs["messages"]
    system = messages[0]["content"]
    assert "额度查询技能名片" in system
    assert "cursor.self/tasks/quota" in system


def test_orchestrator_survives_broken_skill_registry():
    """A bad skill doc raising from SkillRegistry() must not crash the chat
    turn — the orchestrator should soft-fail (no skills this turn) and still
    return the agent's reply.
    """
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    incoming, session_row = _prepare_session(db, "查下我的额度")

    fake_llm = _fake_llm()

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ), patch(
        "assistant_platform.conversation.orchestrator.resolve_capabilities",
        return_value=[],
    ), patch(
        "assistant_platform.conversation.orchestrator.SkillRegistry",
        side_effect=ValueError("bad skill frontmatter"),
    ):
        reply = generate_reply_text(
            db,
            config=_cfg(),
            incoming=incoming,
            text="查下我的额度",
            session_row=session_row,
        )

    assert reply == "ok"
    assert fake_llm.complete_with_tools.called


def test_orchestrator_empty_route_uses_empty_card_messaging():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    incoming, session_row = _prepare_session(db, "今天天气怎么样")

    fake_llm = _fake_llm()

    with patch(
        "assistant_platform.conversation.orchestrator.build_assistant_llm_client",
        return_value=fake_llm,
    ), patch(
        "assistant_platform.conversation.orchestrator.resolve_capabilities",
        return_value=[],
    ), patch(
        "assistant_platform.skills.vector_index.SkillVectorIndex.route_cards",
        return_value=[],
    ):
        generate_reply_text(
            db,
            config=_cfg(),
            incoming=incoming,
            text="今天天气怎么样",
            session_row=session_row,
        )

    messages = fake_llm.complete_with_tools.call_args.kwargs["messages"]
    system = messages[0]["content"]
    assert "本轮未匹配到专项技能" in system
