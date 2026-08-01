"""Shared clock and usage-window constants for Proxy Authorize / Usage Ledger / CRUD."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

WINDOW_5H = timedelta(hours=5)
WINDOW_7D = timedelta(days=7)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Back-compat alias used by web routes and older call sites.
_utcnow = utcnow
