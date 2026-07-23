from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProviderStatus = Literal["succeeded", "failed", "pending", "unknown"]


@dataclass
class CapabilityInvokeRequest:
    invocation_id: str
    idempotency_key: str
    team_id: str
    actor_member_id: str
    capability_key: str
    capability_version: str
    arguments: dict[str, Any] = field(default_factory=dict)
    confirmed_by: str | None = None
    approved_by: str | None = None
    requested_at: str | None = None  # ISO8601


@dataclass
class CapabilityInvokeResult:
    status: ProviderStatus
    user_message: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    retryable: bool = False
    provider_reference: str | None = None
    completed_at: str | None = None
