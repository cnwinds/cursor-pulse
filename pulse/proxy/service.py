from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from pulse.pricing.cursor_tables import get_cursor_pricing_table
from pulse.pricing.types import PricingTable, estimate_token_cost
from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.storage.models import Member, ProxyEvent, ProxyKey, ProxyKeyUsage, KeyLoan, AiAccount

logger = logging.getLogger(__name__)

WINDOW_5H = timedelta(hours=5)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def find_key_by_plaintext(session: Session, plaintext: str) -> ProxyKey | None:
    return session.execute(
        select(ProxyKey).where(ProxyKey.key_hash == hash_proxy_key(plaintext))
    ).scalar_one_or_none()


def create_key(
    session: Session,
    *,
    name: str,
    member_id: str,
    mode: str,
    token_limit: int | None = None,
    cost_limit_cents: int | None = None,
    window_5h_token_limit: int | None = None,
    expires_at: datetime | None = None,
    encryption_key: str = "",
) -> tuple[ProxyKey, str]:
    from pulse.ingestion.crypto import encrypt_secret

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
        mode=mode,
        token_limit=token_limit,
        cost_limit_cents=cost_limit_cents,
        window_5h_token_limit=window_5h_token_limit,
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

def window_usage_tokens(session: Session, proxy_key_id: str, *, now: datetime | None = None) -> int:
    now = now or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    since = now - WINDOW_5H
    # ProxyKeyUsage.ts 统一按 UTC 写入；SQLite 绑参时 tzinfo 被静默丢弃，比较基于 UTC 墙钟
    value = session.execute(
        select(func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0)).where(
            ProxyKeyUsage.proxy_key_id == proxy_key_id,
            ProxyKeyUsage.ts >= since,
        )
    ).scalar_one()
    return int(value)


def total_usage(session: Session, proxy_key_id: str) -> tuple[int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        ).where(ProxyKeyUsage.proxy_key_id == proxy_key_id)
    ).one()
    return int(row[0]), int(row[1])


def loan_proxy_totals(session: Session, loan_id: str) -> tuple[int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        ).where(ProxyKeyUsage.loan_id == loan_id)
    ).one()
    return int(row[0]), int(row[1])


def loan_proxy_usage_summary(
    session: Session,
    loan_id: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict:
    """Aggregate ProxyKeyUsage for a loan, optionally filtered by [start, end).

    Returns request_count, total_tokens, cost_cents, cost_usd, models[], data_updated_at.
    models entries use events/tokens/cost_usd for bot usage formatting.
    """
    clauses = [ProxyKeyUsage.loan_id == loan_id]
    if start is not None:
        clauses.append(ProxyKeyUsage.ts >= start)
    if end is not None:
        clauses.append(ProxyKeyUsage.ts < end)

    rows = list(
        session.execute(
            select(ProxyKeyUsage).where(*clauses).order_by(ProxyKeyUsage.ts.desc())
        )
        .scalars()
        .all()
    )
    by_model: dict[str, dict] = {}
    total_tokens = 0
    cost_cents = 0
    data_updated_at: datetime | None = None
    for u in rows:
        label = (u.model or "").strip() or "（未知）"
        bucket = by_model.get(label)
        if bucket is None:
            bucket = {"model": label, "events": 0, "tokens": 0, "cost_usd": 0.0}
            by_model[label] = bucket
        bucket["events"] += 1
        tokens = int(u.total_tokens or 0)
        cents = int(u.cost_cents or 0)
        bucket["tokens"] += tokens
        bucket["cost_usd"] += cents / 100.0
        total_tokens += tokens
        cost_cents += cents
        if u.ts is not None and (data_updated_at is None or u.ts > data_updated_at):
            data_updated_at = u.ts

    models = sorted(
        by_model.values(),
        key=lambda r: (-r["cost_usd"], -r["events"], r["model"]),
    )
    return {
        "request_count": len(rows),
        "total_tokens": total_tokens,
        "cost_cents": cost_cents,
        "cost_usd": cost_cents / 100.0,
        "models": models,
        "data_updated_at": data_updated_at,
    }


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
    now = now or _utcnow()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    key = find_key_by_plaintext(session, plaintext)
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
    if key.mode == "quota" and key.window_5h_token_limit is not None:
        used = window_usage_tokens(session, key.id, now=now)
        if used >= key.window_5h_token_limit:
            return {"status": "window_limited", **base, "reason": "window_5h_exceeded"}
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
    from pulse.tool_center.key_loans import DELIVERY_PROXY_ALIAS

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
    from pulse.tool_center.key_loans import DELIVERY_PROXY_ALIAS

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


_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write", "reasoning")


def _normalize_tokens(tokens: dict) -> dict:
    """None/负数/缺失统一归一为 >=0 的 int；字符串数字亦可被 int() 接受。"""
    return {name: max(0, int(tokens.get(name) or 0)) for name in _TOKEN_FIELDS}


def canonical_turn_ended_tokens(tokens: dict) -> dict:
    """把 Go TurnEnded 五元组规整为官方 Dashboard 口径。

    TurnEnded field1(Input) 经常是「含 cache 的 input 侧合计」
    （no_cache + cache_write + cache_read），不是 input_no_cache。
    若 input >= cache_read + cache_write 且 cache 非零，则拆出 no_cache；
    否则把 input 当作已经是 no_cache。
    """
    raw = _normalize_tokens(tokens)
    cache_read = raw["cache_read"]
    cache_write = raw["cache_write"]
    inclusive_floor = cache_read + cache_write
    input_raw = raw["input"]
    if inclusive_floor > 0 and input_raw >= inclusive_floor:
        no_cache = input_raw - inclusive_floor
    else:
        no_cache = input_raw
    return {
        "input": no_cache,
        "output": raw["output"],
        "cache_read": cache_read,
        "cache_write": cache_write,
        "reasoning": raw["reasoning"],
    }


def total_tokens_from_canonical(tokens: dict) -> int:
    """canonical 后的总量：与官方 tokens_total 对齐，另含 reasoning。"""
    t = _normalize_tokens(tokens)
    return (
        t["input"]
        + t["output"]
        + t["cache_read"]
        + t["cache_write"]
        + t["reasoning"]
    )


def estimate_cost_cents(
    model: str | None,
    tokens: dict,
    *,
    table: PricingTable | None = None,
) -> int:
    """本地价表估算（美分）。始终先 canonical，避免 raw inclusive input 双计。"""
    canonical = canonical_turn_ended_tokens(tokens)
    est = estimate_token_cost(
        model=model or "",
        max_mode=False,
        tokens_input_no_cache=canonical["input"],
        tokens_input_cache_write=canonical["cache_write"],
        tokens_cache_read=canonical["cache_read"],
        tokens_output=canonical["output"] + canonical["reasoning"],
        table=table or get_cursor_pricing_table(),
    )
    if est is None:
        return 0
    return int(round(est.cost_usd * 100))


def reprice_proxy_usages(
    session: Session,
    *,
    loan_id: str | None = None,
    proxy_key_id: str | None = None,
) -> dict:
    """按 canonical 口径回算 ProxyKeyUsage 的 tokens_input / total_tokens / cost_cents。

    用于修复 TurnEnded inclusive input 双计的历史行；对已正确行幂等（updated=0）。
    """
    query = select(ProxyKeyUsage)
    if loan_id:
        query = query.where(ProxyKeyUsage.loan_id == loan_id)
    if proxy_key_id:
        query = query.where(ProxyKeyUsage.proxy_key_id == proxy_key_id)
    rows = list(session.scalars(query))
    pricing_by_team: dict[str, PricingTable] = {}
    updated = 0
    for row in rows:
        raw = {
            "input": row.tokens_input or 0,
            "output": row.tokens_output or 0,
            "cache_read": row.tokens_cache_read or 0,
            "cache_write": row.tokens_cache_write or 0,
            "reasoning": row.tokens_reasoning or 0,
        }
        tokens = canonical_turn_ended_tokens(raw)
        total = total_tokens_from_canonical(tokens)
        table = _pricing_table_for_usage_row(session, row, pricing_by_team)
        cost = estimate_cost_cents(row.model, tokens, table=table)
        if (
            int(row.tokens_input or 0) == tokens["input"]
            and int(row.total_tokens or 0) == total
            and int(row.cost_cents or 0) == cost
        ):
            continue
        row.tokens_input = tokens["input"]
        row.tokens_output = tokens["output"]
        row.tokens_cache_read = tokens["cache_read"]
        row.tokens_cache_write = tokens["cache_write"]
        row.tokens_reasoning = tokens["reasoning"]
        row.total_tokens = total
        row.cost_cents = cost
        updated += 1
    session.flush()
    return {"scanned": len(rows), "updated": updated}


def _pricing_table_for_usage_row(
    session: Session,
    row: ProxyKeyUsage,
    pricing_by_team: dict[str, PricingTable],
) -> PricingTable | None:
    team_id: str | None = None
    if row.proxy_key_id:
        key = session.get(ProxyKey, row.proxy_key_id)
        if key and key.member_id:
            member = session.get(Member, key.member_id)
            if member:
                team_id = member.team_id
    elif row.loan_id:
        loan = session.get(KeyLoan, row.loan_id)
        if loan:
            account = session.get(AiAccount, loan.source_account_id)
            if account:
                team_id = account.team_id
    if not team_id:
        return None
    table = pricing_by_team.get(team_id)
    if table is None:
        table = get_cursor_pricing_table(session=session, team_id=team_id)
        pricing_by_team[team_id] = table
    return table


def record_usages(
    session: Session, items: list[dict], *, now: datetime | None = None
) -> dict:
    now = now or _utcnow()
    recorded = 0
    touched: set[str] = set()
    pricing_by_team: dict[str, PricingTable] = {}
    for item in items:
        proxy_key_id = item.get("proxy_key_id") or None
        loan_id = item.get("loan_id") or None
        if bool(proxy_key_id) == bool(loan_id):
            continue
        request_id = item.get("request_id")
        tokens = canonical_turn_ended_tokens(item.get("tokens") or {})
        total = total_tokens_from_canonical(tokens)
        ts = item.get("ts")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
        if proxy_key_id:
            key = session.get(ProxyKey, proxy_key_id)
            if key is None:
                continue
            if request_id:
                dup = session.execute(
                    select(ProxyKeyUsage.id).where(
                        ProxyKeyUsage.proxy_key_id == key.id,
                        ProxyKeyUsage.request_id == request_id,
                    )
                ).first()
                if dup is not None:
                    continue
            table = None
            member = session.get(Member, key.member_id) if key.member_id else None
            if member and member.team_id:
                table = pricing_by_team.get(member.team_id)
                if table is None:
                    table = get_cursor_pricing_table(session=session, team_id=member.team_id)
                    pricing_by_team[member.team_id] = table
            session.add(
                ProxyKeyUsage(
                    proxy_key_id=key.id,
                    credential_id=item.get("credential_id"),
                    request_id=request_id,
                    model=item.get("model"),
                    tokens_input=tokens["input"],
                    tokens_output=tokens["output"],
                    tokens_cache_read=tokens["cache_read"],
                    tokens_cache_write=tokens["cache_write"],
                    tokens_reasoning=tokens["reasoning"],
                    total_tokens=total,
                    cost_cents=estimate_cost_cents(item.get("model"), tokens, table=table),
                    ts=ts or now,
                )
            )
            recorded += 1
            touched.add(key.id)
        else:
            loan = session.get(KeyLoan, loan_id)
            if loan is None:
                continue
            if request_id:
                dup = session.execute(
                    select(ProxyKeyUsage.id).where(
                        ProxyKeyUsage.loan_id == loan.id,
                        ProxyKeyUsage.request_id == request_id,
                    )
                ).first()
                if dup is not None:
                    continue
            table = None
            account = session.get(AiAccount, loan.source_account_id)
            if account and account.team_id:
                table = pricing_by_team.get(account.team_id)
                if table is None:
                    table = get_cursor_pricing_table(session=session, team_id=account.team_id)
                    pricing_by_team[account.team_id] = table
            session.add(
                ProxyKeyUsage(
                    proxy_key_id=None,
                    loan_id=loan.id,
                    credential_id=item.get("credential_id"),
                    request_id=request_id,
                    model=item.get("model"),
                    tokens_input=tokens["input"],
                    tokens_output=tokens["output"],
                    tokens_cache_read=tokens["cache_read"],
                    tokens_cache_write=tokens["cache_write"],
                    tokens_reasoning=tokens["reasoning"],
                    total_tokens=total,
                    cost_cents=estimate_cost_cents(item.get("model"), tokens, table=table),
                    ts=ts or now,
                )
            )
            recorded += 1
    session.flush()
    suspended: list[str] = []
    for key_id in sorted(touched):
        key = session.get(ProxyKey, key_id)
        if key is not None and evaluate_key(session, key):
            suspended.append(key_id)
    return {"recorded": recorded, "suspended": suspended}


def evaluate_key(session: Session, key: ProxyKey) -> bool:
    """额度评估，返回是否发生了新的停用。"""
    if key.mode != "quota" or key.status != "active":
        return False
    total_tokens, total_cost = total_usage(session, key.id)
    if key.token_limit is not None and total_tokens >= key.token_limit:
        suspend_key(session, key, "token_limit_exceeded")
        return True
    if key.cost_limit_cents is not None and total_cost >= key.cost_limit_cents:
        suspend_key(session, key, "cost_limit_exceeded")
        return True
    return False


def suspend_key(session: Session, key: ProxyKey, reason: str) -> None:
    if key.status == "suspended":
        return  # 幂等：并发/重复调用不产生重复 suspended 事件
    key.status = "suspended"
    key.suspended_reason = reason
    key.updated_at = _utcnow()
    session.add(ProxyEvent(event_type="suspended", proxy_key_id=key.id, detail=reason))
    # 测试会话 autoflush=False，且调用方可能 refresh(key)；停用状态必须落库
    session.flush()


def resume_key(session: Session, key: ProxyKey) -> bool:
    if key.status != "suspended":
        return False
    total_tokens, total_cost = total_usage(session, key.id)
    if key.token_limit is not None and total_tokens >= key.token_limit:
        return False
    if key.cost_limit_cents is not None and total_cost >= key.cost_limit_cents:
        return False
    key.status = "active"
    key.suspended_reason = None
    key.updated_at = _utcnow()
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
    now = now or _utcnow()
    total_tokens, total_cost = total_usage(session, key.id)
    return {
        "id": key.id,
        "key_hint": key.key_hint,
        "name": key.name,
        "member_id": key.member_id,
        "mode": key.mode,
        "token_limit": key.token_limit,
        "cost_limit_cents": key.cost_limit_cents,
        "window_5h_token_limit": key.window_5h_token_limit,
        "status": key.status,
        "suspended_reason": key.suspended_reason,
        "expires_at": key.expires_at.isoformat() if key.expires_at else None,
        "created_at": key.created_at.isoformat() if key.created_at else None,
        "total_tokens": total_tokens,
        "total_cost_cents": total_cost,
        "window_5h_tokens": window_usage_tokens(session, key.id, now=now),
    }


def _pool_primary_context(session: Session) -> tuple[list, dict, dict, dict]:
    """入池 primary 凭证上下文：(creds, accounts_by_id, latest_snaps, loan_counts)。"""
    from pulse.storage.models import (
        AccountQuotaSnapshot,
        AiAccount,
        AiAccountCredential,
        AiVendor,
        KeyLoan,
    )

    rows = (
        session.execute(
            select(AiAccountCredential)
            .join(AiVendor, AiAccountCredential.vendor_id == AiVendor.id)
            .join(AiAccount, AiAccountCredential.account_id == AiAccount.id)
            .where(
                AiVendor.slug == "cursor",
                AiVendor.is_active.is_(True),
                AiAccount.proxy_enabled.is_(True),
                AiAccount.deleted_at.is_(None),
                AiAccountCredential.status == "active",
                AiAccountCredential.key_role == "primary",
            )
            .order_by(AiAccountCredential.bound_at)
        )
        .scalars()
        .all()
    )
    if not rows:
        return [], {}, {}, {}

    account_ids = list({c.account_id for c in rows})
    accounts = {
        a.id: a
        for a in session.execute(
            select(AiAccount).where(AiAccount.id.in_(account_ids))
        ).scalars()
    }
    latest_snaps: dict = {}
    for snap in session.execute(
        select(AccountQuotaSnapshot)
        .where(AccountQuotaSnapshot.account_id.in_(account_ids))
        .order_by(AccountQuotaSnapshot.captured_at.desc())
    ).scalars():
        if snap.account_id not in latest_snaps:
            latest_snaps[snap.account_id] = snap

    loan_counts: dict = dict(
        session.execute(
            select(KeyLoan.source_account_id, func.count())
            .where(
                KeyLoan.source_account_id.in_(account_ids),
                KeyLoan.status == "active",
            )
            .group_by(KeyLoan.source_account_id)
        ).all()
    )
    return rows, accounts, latest_snaps, loan_counts


def list_pool_credentials(
    session: Session,
    *,
    encryption_key: str,
    loan_selection=None,
) -> list[dict]:
    """返回入池 primary 凭证，按借用推荐分降序；硬过滤不合格账号。"""
    from pulse.ingestion.crypto import decrypt_secret
    from pulse.storage.models import AiAccountCredential
    from pulse.tool_center.burn_rate import LenderCandidate, recommend_lenders

    rows, accounts, latest_snaps, loan_counts = _pool_primary_context(session)
    if not rows:
        return []

    candidates: list[LenderCandidate] = []
    for aid in {c.account_id for c in rows}:
        account = accounts.get(aid)
        snap = latest_snaps.get(aid)
        if not account or not snap:
            continue
        candidates.append(
            LenderCandidate(
                snapshot=snap,
                account_id=aid,
                account_identifier=account.account_identifier,
                renews_on=account.renews_on,
                active_loans=loan_counts.get(aid, 0),
            )
        )

    ranked = recommend_lenders(
        candidates,
        loan_selection=loan_selection,
        enforce_loan_cap=False,
    )
    ranked_ids = [item["account_id"] for item in ranked]
    allowed = set(ranked_ids)

    by_account: dict[str, list[AiAccountCredential]] = {}
    for cred in rows:
        if cred.account_id not in allowed:
            continue
        by_account.setdefault(cred.account_id, []).append(cred)

    enc_key = (encryption_key or "").strip()
    out: list[dict] = []
    for aid in ranked_ids:
        for cred in by_account.get(aid, []):
            try:
                api_key = decrypt_secret(cred.encrypted_value, enc_key)
            except Exception:
                logger.warning("proxy pool: skip credential %s (decrypt failed)", cred.id)
                continue
            out.append({"credential_id": cred.id, "api_key": api_key})
    return out


def list_pool_ranking_board(session: Session, *, loan_selection=None) -> dict:
    """代理池打分看板：入选排序 + 硬过滤排除项（不含密钥）。"""
    from pulse.tool_center.burn_rate import LenderCandidate, explain_lender_selection

    rows, accounts, latest_snaps, loan_counts = _pool_primary_context(session)
    if not rows:
        return {"ranked": [], "excluded": []}

    candidates: list[LenderCandidate] = []
    excluded_no_snap: list[dict] = []
    for aid in sorted({c.account_id for c in rows}):
        account = accounts.get(aid)
        if not account:
            continue
        snap = latest_snaps.get(aid)
        active_loans = loan_counts.get(aid, 0)
        if not snap:
            excluded_no_snap.append(
                {
                    "account_id": aid,
                    "account_identifier": account.account_identifier,
                    "reason": "no_snapshot",
                    "active_loans": active_loans,
                    "status": None,
                    "deadline": None,
                    "hours_to_deadline": None,
                    "renews_on": account.renews_on.isoformat() if account.renews_on else None,
                    "remaining_headroom_pct": None,
                    "total_pct": None,
                }
            )
            continue
        candidates.append(
            LenderCandidate(
                snapshot=snap,
                account_id=aid,
                account_identifier=account.account_identifier,
                renews_on=account.renews_on,
                active_loans=active_loans,
            )
        )

    board = explain_lender_selection(
        candidates,
        loan_selection=loan_selection,
        enforce_loan_cap=False,
    )
    return {
        "ranked": board["ranked"],
        "excluded": excluded_no_snap + board["excluded"],
    }
