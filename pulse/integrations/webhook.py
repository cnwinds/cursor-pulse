from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


def build_bi_payload(*, team_slug: str, team_name: str, period: str, metrics: dict) -> dict:
    return {
        "schema_version": "1.0",
        "source": "cursor-pulse",
        "team": {"slug": team_slug, "name": team_name},
        "period": period,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }


def sign_payload(payload: dict, secret: str) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def push_webhook(url: str, payload: dict, *, secret: str = "", timeout: float = 30.0) -> None:
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["X-Pulse-Signature"] = sign_payload(payload, secret)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(url, headers=headers, json=payload)
        response.raise_for_status()
    logger.info("Pushed BI webhook for period %s", payload.get("period"))
