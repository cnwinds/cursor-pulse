import uuid
from datetime import datetime, timezone

from sqlalchemy import inspect

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.storage.db import init_assistant_db


def test_chat_session_and_message_tables_exist():
    Session = init_assistant_db("sqlite://")
    session = Session()
    try:
        tables = set(inspect(session.get_bind()).get_table_names())
        assert "ap_chat_sessions" in tables
        assert "ap_chat_messages" in tables
    finally:
        session.close()


def test_chat_session_and_message_round_trip():
    Session = init_assistant_db("sqlite://")
    session = Session()
    now = datetime.now(timezone.utc)
    session_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())

    session_row = ChatSessionRow(
        id=session_id,
        assistant_id="xiaomai",
        team_id="team-1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
        status="open",
        opened_at=now,
        last_activity_at=now,
    )
    message_row = ChatMessageRow(
        id=message_id,
        session_id=session_id,
        role="user",
        text_redacted="hello",
        secret_refs_json=[],
        meta_json={},
        created_at=now,
    )
    session.add(session_row)
    session.add(message_row)
    session.commit()

    loaded_session = session.get(ChatSessionRow, session_id)
    loaded_message = session.get(ChatMessageRow, message_id)
    assert loaded_session is not None
    assert loaded_session.status == "open"
    assert loaded_message is not None
    assert loaded_message.role == "user"
    assert loaded_message.text_redacted == "hello"
