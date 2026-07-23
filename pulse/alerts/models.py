from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Alert:
    alert_type: str
    severity: str
    message: str
    member_id: str | None = None
    details: dict | None = None
