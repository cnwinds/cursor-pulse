from __future__ import annotations

from dataclasses import asdict
from typing import Any

import httpx

from assistant_platform.contracts.provider import CapabilityInvokeRequest, CapabilityInvokeResult


class PulseCapabilityClient:
    """HTTP client for Pulse internal capability Provider API."""

    def __init__(
        self,
        *,
        base_url: str,
        internal_token: str,
        http_client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._internal_token = internal_token
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._internal_token}"}

    def get_manifest(self) -> list[dict[str, Any]]:
        response = self._client.get(
            f"{self._base_url}/api/internal/v1/capabilities/manifest",
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        payload = response.json()
        operations = payload.get("operations")
        if not isinstance(operations, list):
            raise ValueError("Invalid manifest response from Pulse")
        return operations

    def invoke(self, request: CapabilityInvokeRequest) -> CapabilityInvokeResult:
        response = self._client.post(
            f"{self._base_url}/api/internal/v1/capabilities/invoke",
            headers=self._auth_headers(),
            json=asdict(request),
        )
        response.raise_for_status()
        data = response.json()
        return CapabilityInvokeResult(
            status=data.get("status", "unknown"),
            user_message=data.get("user_message", ""),
            result=data.get("result") or {},
            error_code=data.get("error_code"),
            retryable=bool(data.get("retryable", False)),
            provider_reference=data.get("provider_reference"),
            completed_at=data.get("completed_at"),
        )
