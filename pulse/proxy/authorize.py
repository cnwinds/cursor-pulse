from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy import key_crud
from pulse.proxy import usage as usage_mod
from pulse.proxy.clock import WINDOW_5H, WINDOW_7D, utcnow
from pulse.proxy.keys import hash_proxy_key
from pulse.storage.models import AiAccountCredential, KeyLoan

logger = logging.getLogger(__name__)


def authorize_status(
    session: Session,
    plaintext: str,
    *,
    now: datetime | None = None,
    encryption_key: str = "",
    loan_selection=None,
) -> dict:
    plaintext = (plaintext or "").strip()
    if plaintext.startswith("pka_"):
        return _authorize_loan_alias(
            session,
            plaintext,
            encryption_key=encryption_key,
            loan_selection=loan_selection,
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


def _authorize_proxy_key(session: Session, plaintext: str, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
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
        expires_at = expires_at.replace(tzinfo=UTC)
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
) -> dict:
    """pka_ 别名 → 固定 Key、候选白名单，或账号池轮换。

    - ``routing_mode=pool``（管理员自动分配）：``mode=loan_pool``，没有 Cursor Key，
      Go 与 pk_ 共用 Credential Pool；
    - ``manual``（指定借用）：返回空白名单，Go 固定在发放时那把 Cursor Key 上；
    - ``auto`` 且仍绑定出借账号（自助借 Key）：返回按打分排序的候选 primary 凭证，
      Go 在白名单内游走。``cursor_api_key`` 是白名单为空时的回退。
    """
    from pulse.ingestion.credentials import CredentialService
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

    if getattr(loan, "routing_mode", None) == "pool":
        # 管理员自动分配：与 pk_ 共用 Credential Pool，不绑定某一把 Cursor Key。
        # 不进授权缓存，撤销后下一次换票即失效。
        return {
            "status": "ok",
            "mode": "loan_pool",
            "proxy_key_id": None,
            "loan_id": loan.id,
            "credential_id": None,
            "reason": None,
        }

    credential_ids = []
    if (getattr(loan, "lender_mode", None) or "") == LENDER_MODE_AUTO:
        credential_ids = _loan_candidate_credential_ids(session, loan, loan_selection=loan_selection)

    cred = session.get(AiAccountCredential, loan.credential_id)
    if cred is None or cred.status != "active" or not cred.encrypted_value:
        # 游走路径不依赖发放时的那把 loan Key：白名单里任一 primary 凭证都能
        # 完成换 JWT，取第一把可解密的下发即可。
        fallback = _decrypt_candidate_credential(session, credential_ids, (encryption_key or "").strip())
        if fallback is not None:
            credential_id, cursor_api_key = fallback
            return {
                "status": "ok",
                "mode": "loan_alias",
                "proxy_key_id": None,
                "loan_id": loan.id,
                "credential_id": credential_id,
                "credential_ids": credential_ids,
                "cursor_api_key": cursor_api_key,
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


def _decrypt_candidate_credential(session: Session, credential_ids: list[str], enc_key: str) -> tuple[str, str] | None:
    """白名单里第一把可解密的 primary 凭证 → ``(credential_id, cursor_api_key)``。

    发放时那把 loan Key 失效（被吊销 / 无密文）时用它兜底：Go 换 JWT 只需要
    一把能登录的 Cursor Key，业务请求随后仍在白名单内游走。
    """
    if not credential_ids or not enc_key:
        return None
    from pulse.ingestion.credentials import CredentialService

    cred_svc = CredentialService(session, enc_key)
    for credential_id in credential_ids:
        candidate = session.get(AiAccountCredential, credential_id)
        if candidate is None or candidate.status != "active" or not candidate.encrypted_value:
            continue
        try:
            return credential_id, cred_svc.decrypt_api_key(candidate)
        except Exception:
            logger.warning("loan alias fallback: credential %s undecryptable", credential_id)
            continue
    return None


def _loan_candidate_credential_ids(
    session: Session,
    loan,
    *,
    loan_selection=None,
) -> list[str]:
    """候选凭证白名单；计算失败不得让授权整体失败（回退到固定绑定）。"""
    try:
        from pulse.proxy.pool_board import loan_candidate_credentials

        return loan_candidate_credentials(
            session,
            loan=loan,
            loan_selection=loan_selection,
            ttl_seconds=float(getattr(loan_selection, "auto_cache_seconds", 600.0) or 600.0),
        )
    except Exception:
        logger.warning(
            "loan %s: candidate credential ranking failed, falling back to bound key",
            getattr(loan, "id", ""),
            exc_info=True,
        )
        return []
