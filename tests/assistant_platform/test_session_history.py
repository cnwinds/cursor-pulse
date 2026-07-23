import uuid
from datetime import datetime, timezone

from assistant_platform.conversation.models import ChatMessageRow, ChatSessionRow
from assistant_platform.conversation.session_history import load_session_history_messages
from assistant_platform.conversation.subject import resolve_subject_id
from assistant_platform.storage.db import init_assistant_db

TEAM = "team-hist"


def test_resolve_subject_id_prefers_member_id():
    assert resolve_subject_id(member_id="m1", channel_user_id="u1") == "m1"
    assert resolve_subject_id(member_id=None, channel_user_id="u1") == "u1"
    assert resolve_subject_id(member_id="", channel_user_id="u1") == "u1"


def test_history_only_loads_named_session_user_assistant():
    Session = init_assistant_db("sqlite://", team_id=TEAM)
    db = Session()
    s_a = ChatSessionRow(
        id=str(uuid.uuid4()),
        assistant_id="xiaomai",
        team_id=TEAM,
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u-a",
        user_id="u-a",
        status="open",
        last_activity_at=datetime.now(timezone.utc),
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
        last_activity_at=datetime.now(timezone.utc),
    )
    db.add_all([s_a, s_b])
    db.add_all(
        [
            ChatMessageRow(
                session_id=s_a.id,
                role="user",
                text_redacted="A问",
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_a.id,
                role="assistant",
                text_redacted="A答",
                secret_refs_json=[],
                meta_json={},
            ),
            ChatMessageRow(
                session_id=s_b.id,
                role="user",
                text_redacted="B机密",
                secret_refs_json=[],
                meta_json={},
            ),
        ]
    )
    db.commit()

    messages = load_session_history_messages(db, session_id=s_a.id, limit=40)
    texts = [m["content"] for m in messages]
    assert texts == ["A问", "A答"]
    assert "B机密" not in texts
