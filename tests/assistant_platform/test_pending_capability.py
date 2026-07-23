from datetime import datetime, timedelta, timezone

from assistant_platform.conversation.models import ChatSessionRow
from assistant_platform.conversation.pending import (
    clear_pending_capability,
    get_pending_capability,
    set_pending_capability,
)


def _session() -> ChatSessionRow:
    return ChatSessionRow(
        assistant_id="xiaomai",
        team_id="t1",
        channel="dingtalk",
        conversation_type="private",
        conversation_id="u1",
        user_id="u1",
    )


def test_set_and_get_pending():
    row = _session()
    set_pending_capability(
        row,
        capability_key="cursor.key.bind",
        arguments={"text": "绑定", "api_key": "sk-x"},
    )
    pending = get_pending_capability(row)
    assert pending is not None
    assert pending["capability_key"] == "cursor.key.bind"


def test_pending_expires_after_ttl():
    row = _session()
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    row.session_state_json = {
        "pending_capability": {
            "capability_key": "cursor.key.bind",
            "arguments": {"text": "x"},
            "created_at": past,
        }
    }
    assert get_pending_capability(row, ttl_seconds=300) is None
    assert row.session_state_json.get("pending_capability") is None


def test_clear_pending():
    row = _session()
    set_pending_capability(row, capability_key="k", arguments={"text": "x"})
    clear_pending_capability(row)
    assert get_pending_capability(row) is None
