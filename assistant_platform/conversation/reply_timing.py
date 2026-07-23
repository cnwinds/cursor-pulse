from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ReplyTurnTimer:
    """Per-turn monotonic timer for reply pipeline diagnostics."""

    def __init__(self, *, session_id: str, trigger_message_id: str) -> None:
        self.session_id = session_id
        self.trigger_message_id = trigger_message_id
        self.started_at = datetime.now(timezone.utc).isoformat()
        self._t0 = time.monotonic()

    def mark(self, stage: str, **fields: object) -> None:
        elapsed_ms = int((time.monotonic() - self._t0) * 1000)
        extras = " ".join(f"{key}={value}" for key, value in fields.items())
        logger.info(
            "reply.timing stage=%s session_id=%s trigger_message_id=%s "
            "turn_started_at=%s elapsed_ms=%d %s",
            stage,
            self.session_id,
            self.trigger_message_id,
            self.started_at,
            elapsed_ms,
            extras.strip(),
        )
