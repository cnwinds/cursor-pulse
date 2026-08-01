from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy import key_crud
from pulse.proxy import usage as usage_mod
from pulse.proxy.clock import WINDOW_5H, WINDOW_7D, utcnow
from pulse.proxy.keys import hash_proxy_key
from pulse.storage.models import AiAccountCredential, KeyLoan, ProxyKey
from pulse.util.datetime_fmt import tool_datetime

logger = logging.getLogger(__name__)


def authorize_status(
    session: Session,
    plaintext: str,
    *,
    now: datetime | None = None,
    encryption_key: str = "",
) -> dict:
    plaintext = (plaintext or "").strip()
    if plaintext.startswith("pka_"):
        return _authorize_loan_alias(session, plaintext, encryption_key=encryption_key)
    if plaintext.startswith("pk_"):
        return _authorize_proxy_key(session, plaintext, now=now)
    if plaintext.startswith("cr"):
        return _authorize_loan_passthrough(session, plaintext)
    return {
        "status": "invalid",
        "proxy_key_id": None,
        "mode": None,
        "loan_id": None,
        "credential_id": None,
        "reason": "unknown_key",
    }


def _authorize_proxy_key(
    session: Session, plaintext: str, *, now: datetime | None = None
) -> dict:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    key = key_crud.find_key_by_plaintext(session, plaintext)
    if key is None:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": None,
            "credential_id": None,
            "reason": "unknown_key",
        }
    base = {
        "proxy_key_id": key.id,
        "mode": key.mode,
        "loan_id": None,
        "credential_id": None,
    }
    if key.status == "revoked":
        return {"status": "invalid", **base, "reason": "revoked"}
    expires_at = key.expires_at
    if expires_at is not None and expires_at.tzinfo is None:
        # SQLite 不保留 tzinfo，按 UTC 归一化后再比较
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at is not None and expires_at <= now:
        return {"status": "invalid", **base, "reason": "expired"}
    if key.status == "suspended":
        return {"status": "suspended", **base, "reason": key.suspended_reason or "suspended"}
    if key.window_5h_cost_limit_cents is not None:
        used_5h = usage_mod.window_usage_cost(session, key.id, window=WINDOW_5H, now=now)
        if used_5h >= key.window_5h_cost_limit_cents:
            return {"status": "window_limited", **base, "reason": "window_5h_exceeded"}
    if key.window_7d_cost_limit_cents is not None:
        used_7d = usage_mod.window_usage_cost(session, key.id, window=WINDOW_7D, now=now)
        if used_7d >= key.window_7d_cost_limit_cents:
            return {"status": "window_limited", **base, "reason": "window_7d_exceeded"}
    return {"status": "ok", **base, "reason": None}


def _authorize_loan_passthrough(session: Session, plaintext: str) -> dict:
    from pulse.storage.models import AiAccountCredential, KeyLoan

    h = hash_proxy_key(plaintext)
    cred = session.scalar(
        select(AiAccountCredential).where(
            AiAccountCredential.key_hash == h,
            AiAccountCredential.status == "active",
            AiAccountCredential.key_role == "loan",
        )
    )
    if cred is None:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": None,
            "credential_id": None,
            "reason": "unknown_key",
        }
    loan = session.scalar(
        select(KeyLoan).where(
            KeyLoan.credential_id == cred.id,
            KeyLoan.status == "active",
        )
    )
    if loan is None:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": None,
            "credential_id": cred.id,
            "reason": "loan_inactive",
        }
    from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS

    # proxy_alias 交付的底层 cr* 不允许客户端直连透传（须用 pka_）
    if (getattr(loan, "delivery_mode", None) or "") == DELIVERY_PROXY_ALIAS:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": loan.id,
            "credential_id": cred.id,
            "reason": "alias_required",
        }
    return {
        "status": "ok",
        "mode": "loan_passthrough",
        "proxy_key_id": None,
        "loan_id": loan.id,
        "credential_id": cred.id,
        "reason": None,
    }


def _authorize_loan_alias(
    session: Session, plaintext: str, *, encryption_key: str = ""
) -> dict:
    """pka_ 别名 → 解密绑定的 Cursor Key，供 Go 换 JWT（不进共享池）。"""
    from pulse.ingestion.credentials import CredentialService
    from pulse.storage.models import AiAccountCredential, KeyLoan
    from pulse.tool_center.key_loan_delivery import DELIVERY_PROXY_ALIAS

    h = hash_proxy_key(plaintext)
    loan = session.scalar(
        select(KeyLoan).where(
            KeyLoan.alias_key_hash == h,
            KeyLoan.status == "active",
            KeyLoan.delivery_mode == DELIVERY_PROXY_ALIAS,
        )
    )
    if loan is None:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": None,
            "credential_id": None,
            "reason": "unknown_key",
        }

    cred = session.get(AiAccountCredential, loan.credential_id)
    if cred is None or cred.status != "active" or not cred.encrypted_value:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": "loan_alias",
            "loan_id": loan.id,
            "credential_id": loan.credential_id,
            "reason": "credential_unavailable",
        }

    enc_key = (encryption_key or "").strip()
    if not enc_key:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": "loan_alias",
            "loan_id": loan.id,
            "credential_id": cred.id,
            "reason": "encryption_unavailable",
        }
    try:
        cred_svc = CredentialService(session, enc_key)
        cursor_api_key = cred_svc.decrypt_api_key(cred)
    except Exception:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": "loan_alias",
            "loan_id": loan.id,
            "credential_id": cred.id,
            "reason": "credential_undecryptable",
        }
    return {
        "status": "ok",
        "mode": "loan_alias",
        "proxy_key_id": None,
        "loan_id": loan.id,
        "credential_id": cred.id,
        "cursor_api_key": cursor_api_key,
        "reason": None,
    }

