from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from assistant_platform.config import AssistantConfig
from assistant_platform.integrations.channel_reply import send_channel_stream

_DEFAULT_MIN_INTERVAL_SECONDS = 0.3


class WebReplyStream:
    """Pushes the LLM's in-progress reply to the web chat as a throttled live draft.

    Each draft is identified by a ``stream_id``; the committed interim/final reply
    carries that id (``take_stream_id``) so Pulse swaps the draft for the real
    message atomically.
    """

    def __init__(
        self,
        *,
        config: AssistantConfig,
        session_id: str,
        reply_endpoint: dict[str, Any],
        min_interval_seconds: float = _DEFAULT_MIN_INTERVAL_SECONDS,
        send: Callable[[dict[str, Any], AssistantConfig], dict[str, Any]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._session_id = session_id
        self._reply_endpoint = reply_endpoint
        self._min_interval = max(0.0, min_interval_seconds)
        self._send = send if send is not None else send_channel_stream
        self._clock = clock
        self._stream_id: str | None = None
        self._last_sent_at: float | None = None

    def update(self, text: str) -> None:
        if not text:
            return
        if self._stream_id is None:
            self._stream_id = uuid.uuid4().hex
            self._last_sent_at = None
        now = self._clock()
        if self._last_sent_at is not None and now - self._last_sent_at < self._min_interval:
            return
        self._last_sent_at = now
        self._post(text=text, done=False)

    def discard(self) -> None:
        if self._stream_id is None:
            return
        self._post(text="", done=True)
        self._stream_id = None

    def take_stream_id(self) -> str | None:
        stream_id, self._stream_id = self._stream_id, None
        return stream_id

    def _post(self, *, text: str, done: bool) -> None:
        self._send(
            {
                "session_id": self._session_id,
                "stream_id": self._stream_id,
                "reply_endpoint": self._reply_endpoint,
                "text": text,
                "done": done,
            },
            self._config,
        )
