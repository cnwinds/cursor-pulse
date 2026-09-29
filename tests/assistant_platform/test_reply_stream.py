from __future__ import annotations

from assistant_platform.config import AssistantConfig
from assistant_platform.integrations.reply_stream import WebReplyStream


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _stream(sent: list[dict], clock: _Clock) -> WebReplyStream:
    return WebReplyStream(
        config=AssistantConfig(),
        session_id="sess-1",
        reply_endpoint={"channel": "web", "member_id": "m1"},
        min_interval_seconds=0.3,
        send=lambda payload, _cfg: sent.append(payload) or {"status": "ok"},
        clock=clock,
    )


def test_update_throttles_and_sends_accumulated_text():
    sent: list[dict] = []
    clock = _Clock()
    stream = _stream(sent, clock)

    stream.update("你")
    clock.now = 0.1
    stream.update("你好")
    clock.now = 0.4
    stream.update("你好呀")

    assert [p["text"] for p in sent] == ["你", "你好呀"]
    assert sent[0]["stream_id"] == sent[1]["stream_id"]
    assert sent[0]["done"] is False
    assert sent[0]["reply_endpoint"] == {"channel": "web", "member_id": "m1"}


def test_take_stream_id_hands_off_draft_and_next_update_starts_new_stream():
    sent: list[dict] = []
    clock = _Clock()
    stream = _stream(sent, clock)

    stream.update("草稿")
    first_id = stream.take_stream_id()
    assert first_id == sent[0]["stream_id"]
    assert stream.take_stream_id() is None

    stream.discard()
    assert len(sent) == 1

    stream.update("新一轮")
    assert sent[-1]["stream_id"] != first_id


def test_discard_closes_active_draft_once():
    sent: list[dict] = []
    stream = _stream(sent, _Clock())

    stream.update("好的")
    stream.discard()
    stream.discard()

    assert [(p["text"], p["done"]) for p in sent] == [("好的", False), ("", True)]
    assert stream.take_stream_id() is None
