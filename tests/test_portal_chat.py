from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pulse.web.portal_chat import list_portal_chat_deliveries, store_portal_chat_delivery
from pulse.storage.db import init_db


def test_portal_chat_delivery_roundtrip():
    Session = init_db("sqlite://")
    db = Session()
    row = store_portal_chat_delivery(
        db,
        team_id="team-1",
        member_id="member-1",
        text="进度更新",
        kind="interim",
        assistant_session_id="sess-1",
        assistant_message_id="msg-1",
    )
    db.commit()
    assert row.id > 0

    items = list_portal_chat_deliveries(
        db, team_id="team-1", member_id="member-1", after_id=0
    )
    assert len(items) == 1
    assert items[0].text == "进度更新"
    assert items[0].kind == "interim"

    later = list_portal_chat_deliveries(
        db, team_id="team-1", member_id="member-1", after_id=row.id
    )
    assert later == []
