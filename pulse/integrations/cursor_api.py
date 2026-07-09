from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api2.cursor.sh"


@dataclass
class UsageEventDTO:
    event_at: datetime
    event_date: datetime.date
    model: str
    kind: str
    tokens_input_cache_write: int
    tokens_input_no_cache: int
    tokens_cache_read: int
    tokens_output: int
    tokens_total: int
    cost_usd: float
    cost_raw: str
    external_id: str
    source_row_hash: str


def map_usage_event(raw: dict) -> UsageEventDTO:
    ts_ms = int(raw["timestamp"])
    event_at = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    token = raw.get("tokenUsage") or {}
    input_t = int(token.get("inputTokens") or 0)
    output_t = int(token.get("outputTokens") or 0)
    cache_read = int(token.get("cacheReadTokens") or 0)
    cache_write = int(token.get("cacheWriteTokens") or 0)
    cents = float(raw.get("chargedCents") or token.get("totalCents") or 0)
    conv = raw.get("conversationId") or ""
    external_id = hashlib.sha256(
        f"{raw['timestamp']}:{raw.get('model')}:{conv}".encode()
    ).hexdigest()[:32]
    kind = raw.get("kind") or "unknown"
    cost_raw = "included" if "INCLUDED" in kind else "usage_based"
    return UsageEventDTO(
        event_at=event_at,
        event_date=event_at.date(),
        model=raw.get("model") or "unknown",
        kind=kind,
        tokens_input_cache_write=cache_write,
        tokens_input_no_cache=input_t,
        tokens_cache_read=cache_read,
        tokens_output=output_t,
        tokens_total=input_t + output_t + cache_read + cache_write,
        cost_usd=round(cents / 100.0, 6),
        cost_raw=cost_raw,
        external_id=external_id,
        source_row_hash=external_id,
    )


class CursorApiClient:
    def __init__(self, api_base: str = API_BASE, timeout: float = 30.0):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

    def exchange_api_key(self, api_key: str) -> str:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.api_base}/auth/exchange_user_api_key",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={},
            )
            resp.raise_for_status()
            data = resp.json()
            token = data.get("accessToken")
            if not token:
                raise ValueError("exchange returned no accessToken")
            return token

    def _post_dashboard(self, token: str, method: str, body: dict) -> dict:
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.api_base}/aiserver.v1.DashboardService/{method}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Connect-Protocol-Version": "1",
                },
                json=body,
            )
            resp.raise_for_status()
            return resp.json()

    def get_current_period_usage(self, token: str) -> dict:
        return self._post_dashboard(token, "GetCurrentPeriodUsage", {})

    def iter_filtered_usage_events(
        self,
        token: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        page_size: int = 100,
    ):
        page = 1
        body: dict = {"page": page, "pageSize": page_size}
        if start_ms is not None:
            body["startDate"] = str(start_ms)
        if end_ms is not None:
            body["endDate"] = str(end_ms)
        while True:
            body["page"] = page
            data = self._post_dashboard(token, "GetFilteredUsageEvents", body)
            events = data.get("usageEventsDisplay") or []
            for raw in events:
                yield map_usage_event(raw)
            if not events:
                break
            page += 1
