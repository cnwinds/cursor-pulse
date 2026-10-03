"""Authorize pkcp_ keys for Coding Plan OpenAI gateway."""

from __future__ import annotations

from datetime import UTC, datetime

from pulse.openai_proxy.upstream import CP_VENDORS
from pulse.proxy import key_crud
from pulse.proxy import usage as usage_mod
from pulse.proxy.clock import WINDOW_5H, WINDOW_7D, utcnow
from sqlalchemy.orm import Session


def authorize_pkcp(session: Session, plaintext: str, *, now: datetime | None = None) -> dict:
    plaintext = (plaintext or "").strip()
    if not plaintext.startswith("pkcp_"):
        return _invalid("unknown_key")
    key = key_crud.find_key_by_plaintext(session, plaintext)
    if key is None:
        return _invalid("unknown_key")
    vendor = (key.coding_plan_vendor or "").strip().lower()
    base = {
        "status": "invalid",
        "proxy_key_id": key.id,
        "coding_plan_vendor": vendor or None,
        "reason": None,
    }
    if key.mode != "coding_plan" or vendor not in CP_VENDORS:
        return {**base, "reason": "not_coding_plan_key"}
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    if key.status == "revoked":
        return {**base, "reason": "revoked"}
    expires_at = key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if expires_at is not None and expires_at <= now:
        return {**base, "reason": "expired"}
    if key.status == "suspended":
        return {**base, "status": "suspended", "reason": key.suspended_reason or "suspended"}
    if key.window_5h_cost_limit_cents is not None:
        used_5h = usage_mod.window_usage_cost(session, key.id, window=WINDOW_5H, now=now)
        if used_5h >= key.window_5h_cost_limit_cents:
            return {**base, "status": "window_limited", "reason": "window_5h_exceeded"}
    if key.window_7d_cost_limit_cents is not None:
        used_7d = usage_mod.window_usage_cost(session, key.id, window=WINDOW_7D, now=now)
        if used_7d >= key.window_7d_cost_limit_cents:
            return {**base, "status": "window_limited", "reason": "window_7d_exceeded"}
    return {
        "status": "ok",
        "proxy_key_id": key.id,
        "coding_plan_vendor": vendor,
        "reason": None,
    }


def _invalid(reason: str) -> dict:
    return {
        "status": "invalid",
        "proxy_key_id": None,
        "coding_plan_vendor": None,
        "reason": reason,
    }
