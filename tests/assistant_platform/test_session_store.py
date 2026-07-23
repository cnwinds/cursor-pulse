import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_store import (
    GROUP_IDLE,
    PRIVATE_IDLE,
    attach_user_message,
    close_session,
    get_open_session,
    session_key_fields,
)
from assistant_platform.domain.events import IncomingMessageEvent
from assistant_platform.storage.db import init_assistant_db


def _event(
    *,
    msg_id: str = "m-1",
    text: str = "hello",
    conversation_type: str = "private",
    conversation_id: str = "u1",
    sender: str = "u1",
    team_id: str = "team-1",
) -> IncomingMessageEvent:
    return IncomingMessageEvent(
        event_id=str(uuid.uuid4()),
        channel="dingtalk",
        channel_message_id=msg_id,
        assistant_id="xiaomai",
        team_id=team_id,
        sender_channel_user_id=sender,
        sender_display_name="Alice",
        conversation_type=conversation_type,
        conversation_id=conversation_id,
        text_redacted=text,
        occurred_at=datetime.now(timezone.utc),
    )


def test_session_key_fields_private_includes_user_id():
    key = session_key_fields(_event(conversation_type="private", sender="u42"))
    assert key == {
        "assistant_id": "xiaomai",
        "team_id": "team-1",
        "channel": "dingtalk",
        "conversation_type": "private",
        "conversation_id": "u42",
        "user_id": "u42",
    }


def test_session_key_fields_group_user_id_is_none():
    key = session_key_fields(
        _event(conversation_type="group", conversation_id="g1", sender="u42")
    )
    assert key["conversation_type"] == "group"
    assert key["conversation_id"] == "g1"
    assert key["user_id"] is None


def test_attach_creates_new_open_session_and_user_message():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    event = _event(msg_id="m-new", text="first message")

    session_row, message_row = attach_user_message(session, event, now=now)
    session.commit()

    assert session_row.status == "open"
    assert session_row.user_id == "u1"
    assert session_row.opened_at == now
    assert session_row.last_activity_at == now
    assert message_row.role == "user"
    assert message_row.text_redacted == "first message"
    assert message_row.session_id == session_row.id


def test_attach_continues_private_session_when_conversation_id_differs():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    first_session, _ = attach_user_message(
        session,
        _event(msg_id="m-a", sender="u1", conversation_id="dingtalk-cid-xyz"),
        now=now,
    )
    second_session, _ = attach_user_message(
        session,
        _event(msg_id="m-b", sender="u1", conversation_id="u1", text="确认"),
        now=now + timedelta(seconds=5),
    )
    session.commit()

    assert second_session.id == first_session.id


def test_attach_continues_open_session_within_idle_window():
    Session = init_assistant_db("sqlite://")
    session = Session()
    t0 = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(minutes=5)

    first_session, _ = attach_user_message(session, _event(msg_id="m-a"), now=t0)
    second_session, second_message = attach_user_message(
        session, _event(msg_id="m-b", text="follow up"), now=t1
    )
    session.commit()

    assert second_session.id == first_session.id
    assert second_session.last_activity_at == t1
    assert second_message.session_id == first_session.id

    message_count = session.scalar(
        select(func.count()).select_from(ChatMessageRow).where(
            ChatMessageRow.session_id == first_session.id
        )
    )
    assert message_count == 2


def test_attach_closes_idle_private_session_and_opens_new():
    Session = init_assistant_db("sqlite://")
    session = Session()
    t0 = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + PRIVATE_IDLE + timedelta(seconds=1)

    first_session, _ = attach_user_message(session, _event(msg_id="m-old"), now=t0)
    second_session, _ = attach_user_message(session, _event(msg_id="m-new"), now=t1)
    session.commit()

    closed = session.get(ChatSessionRow, first_session.id)
    assert closed is not None
    assert closed.status == "closed"
    assert closed.close_reason == "idle_timeout"
    assert closed.closed_at == t1
    assert second_session.id != first_session.id
    assert second_session.status == "open"


def test_attach_closes_idle_group_session_after_shorter_timeout():
    Session = init_assistant_db("sqlite://")
    session = Session()
    t0 = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    within_group_idle = t0 + timedelta(minutes=5)

    group_event = _event(
        msg_id="g-a",
        conversation_type="group",
        conversation_id="g1",
        sender="u1",
    )
    first_session, _ = attach_user_message(session, group_event, now=t0)
    continued, _ = attach_user_message(
        session,
        _event(
            msg_id="g-b",
            conversation_type="group",
            conversation_id="g1",
            sender="u2",
        ),
        now=within_group_idle,
    )
    session.commit()
    assert continued.id == first_session.id

    beyond_group_idle = within_group_idle + GROUP_IDLE + timedelta(seconds=1)
    session = Session()
    reopened, _ = attach_user_message(
        session,
        _event(
            msg_id="g-c",
            conversation_type="group",
            conversation_id="g1",
            sender="u3",
        ),
        now=beyond_group_idle,
    )
    session.commit()
    assert reopened.id != first_session.id

    closed = session.get(ChatSessionRow, first_session.id)
    assert closed is not None
    assert closed.status == "closed"
    assert closed.close_reason == "idle_timeout"


def test_private_sessions_isolated_by_user_id():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    s1, _ = attach_user_message(
        session,
        _event(msg_id="u1-msg", sender="u1", conversation_id="u1"),
        now=now,
    )
    s2, _ = attach_user_message(
        session,
        _event(msg_id="u2-msg", sender="u2", conversation_id="u2"),
        now=now,
    )
    session.commit()

    assert s1.id != s2.id
    assert s1.user_id == "u1"
    assert s2.user_id == "u2"


def test_get_open_session_returns_none_when_closed():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    event = _event()

    session_row, _ = attach_user_message(session, event, now=now)
    close_session(session, session_row, reason="manual", now=now)
    session.commit()

    key = session_key_fields(event)
    found = get_open_session(
        session,
        assistant_id=key["assistant_id"],
        team_id=key["team_id"],
        channel=key["channel"],
        conversation_type=key["conversation_type"],
        conversation_id=key["conversation_id"],
        user_id=key["user_id"],
    )
    assert found is None


def test_attach_stores_incoming_event_id_on_message():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
    incoming_id = str(uuid.uuid4())

    _, message_row = attach_user_message(
        session,
        _event(msg_id="m-ref"),
        incoming_event_id=incoming_id,
        now=now,
    )
    session.commit()

    assert message_row.incoming_event_id == incoming_id
