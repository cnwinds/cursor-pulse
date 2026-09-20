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
    loan_selection=None,
    jev=None,
) -> dict:
    plaintext = (plaintext or "").strip()
    if plaintext.startswith("pka_"):
        return _authorize_loan_alias(
            session,
            plaintext,
            encryption_key=encryption_key,
            loan_selection=loan_selection,
            jev=jev,
        )
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
    session: Session,
    plaintext: str,
    *,
    encryption_key: str = "",
    loan_selection=None,
    jev=None,
) -> dict:
    """pka_ 别名 → 绑定的 Cursor Key + 可游走候选凭证白名单。

    两套机制并存，由 ``loan.lender_mode`` 区分：

    - ``manual``（指定借用）：返回空白名单，Go 固定在发放时那把 Cursor Key 上；
    - ``auto``（自动分配借用）：返回按打分排序的候选 primary 凭证，Go 在借用
      路径上按共享池的方式选号（sticky + Switch dwell + 按 Quota Pool）。
      ``cursor_api_key`` 仍是白名单为空时的回退（排名失败 / 无候选）。
    """
    from pulse.ingestion.credentials import CredentialService
    from pulse.storage.models import AiAccountCredential, KeyLoan
    from pulse.tool_center.key_loan_delivery import (
        DELIVERY_PROXY_ALIAS,
        LENDER_MODE_AUTO,
    )

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

    credential_ids = []
    if (getattr(loan, "lender_mode", None) or "") == LENDER_MODE_AUTO:
        credential_ids = _loan_candidate_credential_ids(
            session, loan, loan_selection=loan_selection, jev=jev
        )

    cred = session.get(AiAccountCredential, loan.credential_id)
    if cred is None or cred.status != "active" or not cred.encrypted_value:
        # 游走路径不依赖发放时的那把 loan Key；只要白名单可用就仍可服务
        if credential_ids:
            return {
                "status": "ok",
                "mode": "loan_alias",
                "proxy_key_id": None,
                "loan_id": loan.id,
                "credential_id": credential_ids[0],
                "credential_ids": credential_ids,
                "reason": None,
            }
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
        "credential_ids": credential_ids,
        "cursor_api_key": cursor_api_key,
        "reason": None,
    }


def _loan_candidate_credential_ids(
    session: Session,
    loan,
    *,
    loan_selection=None,
    jev=None,
) -> list[str]:
    """候选凭证白名单；计算失败不得让授权整体失败（回退到固定绑定）。"""
    try:
        from pulse.proxy.pool_board import loan_candidate_credentials

        return loan_candidate_credentials(
            session,
            loan=loan,
            loan_selection=loan_selection,
            jev=jev,
            ttl_seconds=float(
                getattr(loan_selection, "auto_cache_seconds", 600.0) or 600.0
            ),
        )
    except Exception:
        logger.warning(
            "loan %s: candidate credential ranking failed, falling back to bound key",
            getattr(loan, "id", ""),
            exc_info=True,
        )
        return []

