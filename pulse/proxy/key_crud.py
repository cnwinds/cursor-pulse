from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.proxy.clock import WINDOW_5H, WINDOW_7D, utcnow
from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.proxy import usage as usage_mod
from pulse.storage.models import ProxyEvent, ProxyKey
from pulse.util.datetime_fmt import serialize_datetime

logger = logging.getLogger(__name__)

# Re-export for authorize / facade callers.
_utcnow = utcnow


def usd_to_cents(usd: int | None) -> int | None:
    if usd is None:
        return None
    return int(usd) * 100


def cents_to_usd(cents: int | None) -> int | None:
    if cents is None:
        return None
    return int(cents) // 100


def find_key_by_plaintext(session: Session, plaintext: str) -> ProxyKey | None:
    return session.execute(
        select(ProxyKey).where(ProxyKey.key_hash == hash_proxy_key(plaintext))
    ).scalar_one_or_none()


def create_key(
    session: Session,
    *,
    name: str,
    member_id: str,
    window_5h_cost_limit_cents: int | None = None,
    window_7d_cost_limit_cents: int | None = None,
    expires_at: datetime | None = None,
    encryption_key: str = "",
    mode: str = "quota",  # ignored; empty windows = unlimited
) -> tuple[ProxyKey, str]:
    from pulse.ingestion.crypto import encrypt_secret

    _ = mode
    plaintext, key_hash, hint = generate_proxy_key()
    encrypted = None
    if encryption_key.strip():
        encrypted = encrypt_secret(plaintext, encryption_key.strip())
    key = ProxyKey(
        key_hash=key_hash,
        key_hint=hint,
        encrypted_key=encrypted,
        name=name,
        member_id=member_id,
        mode="quota",
        window_5h_cost_limit_cents=window_5h_cost_limit_cents,
        window_7d_cost_limit_cents=window_7d_cost_limit_cents,
        expires_at=expires_at,
    )
    session.add(key)
    session.flush()
    return key, plaintext


def reveal_plaintext(key: ProxyKey, encryption_key: str) -> str | None:
    """还原明文；无密文或解密失败返回 None。"""
    if not key.encrypted_key or not encryption_key.strip():
        return None
    from pulse.ingestion.crypto import decrypt_secret

    try:
        return decrypt_secret(key.encrypted_key, encryption_key.strip())
    except Exception:
        return None


def build_client_command(*, shell: str, proxy_url: str, plaintext_key: str) -> str:
    url = proxy_url.rstrip("/")
    if shell == "powershell":
        return (
            f'$env:HTTPS_PROXY = "{url}"\n'
            f'$env:CURSOR_API_KEY = "{plaintext_key}"\n'
            "agent -k"
        )
    # bash / linux / macos
    return (
        f'export HTTPS_PROXY="{url}"\n'
        f'export CURSOR_API_KEY="{plaintext_key}"\n'
        "agent -k"
    )


def evaluate_key(session: Session, key: ProxyKey) -> bool:
    """额度评估。窗口超限走 authorize soft-reject，不再 suspend。"""
    del session, key
    return False


def suspend_key(session: Session, key: ProxyKey, reason: str) -> None:
    if key.status == "suspended":
        return  # 幂等：并发/重复调用不产生重复 suspended 事件
    key.status = "suspended"
    key.suspended_reason = reason
    key.updated_at = utcnow()
    session.add(ProxyEvent(event_type="suspended", proxy_key_id=key.id, detail=reason))
    # 测试会话 autoflush=False，且调用方可能 refresh(key)；停用状态必须落库
    session.flush()


def resume_key(session: Session, key: ProxyKey) -> bool:
    if key.status != "suspended":
        return False
    key.status = "active"
    key.suspended_reason = None
    key.updated_at = utcnow()
    session.add(ProxyEvent(event_type="resumed", proxy_key_id=key.id))
    # 与 suspend_key 同理：恢复状态与事件需立即对后续查询可见
    session.flush()
    return True


def record_event(
    session: Session,
    *,
    event_type: str,
    proxy_key_id: str | None = None,
    loan_id: str | None = None,
    credential_id: str | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        ProxyEvent(
            event_type=event_type,
            proxy_key_id=proxy_key_id,
            loan_id=loan_id,
            credential_id=credential_id,
            detail=detail,
        )
    )



def key_summary(session: Session, key: ProxyKey, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    total_tokens, total_cost = usage_mod.total_usage(session, key.id)
    used_5h = usage_mod.window_usage_cost(session, key.id, window=WINDOW_5H, now=now)
    used_7d = usage_mod.window_usage_cost(session, key.id, window=WINDOW_7D, now=now)
    return {
        "id": key.id,
        "key_hint": key.key_hint,
        "name": key.name,
        "member_id": key.member_id,
        "mode": key.mode,
        "window_5h_cost_limit_cents": key.window_5h_cost_limit_cents,
        "window_7d_cost_limit_cents": key.window_7d_cost_limit_cents,
        "window_5h_cost_usd": cents_to_usd(key.window_5h_cost_limit_cents),
        "window_7d_cost_usd": cents_to_usd(key.window_7d_cost_limit_cents),
        "status": key.status,
        "suspended_reason": key.suspended_reason,
        "expires_at": serialize_datetime(key.expires_at),
        "created_at": serialize_datetime(key.created_at),
        "total_tokens": total_tokens,
        "total_cost_cents": total_cost,
        "window_5h_cost_cents": used_5h,
        "window_7d_cost_cents": used_7d,
    }
