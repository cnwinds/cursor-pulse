from __future__ import annotations

from pulse.web.internal_channel_api import _already_delivered, _dedupe_key


def test_dedupe_key_blocks_second_delivery():
    key = _dedupe_key(message_id="msg-1", text="hello", kind="interim")
    assert key == "msg-1:interim"
    assert _already_delivered(key) is False
    assert _already_delivered(key) is True
