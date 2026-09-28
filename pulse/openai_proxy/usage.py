"""Usage ledger for Coding Plan OpenAI gateway."""

from __future__ import annotations

import uuid
from typing import Any

from pulse.pricing.cursor_tables import get_cursor_pricing_table
from pulse.proxy.clock import utcnow
from pulse.proxy.usage import estimate_cost_cents
from pulse.storage.models import Member, ProxyKey, ProxyKeyUsage
from sqlalchemy.orm import Session


def parse_openai_usage(body: dict[str, Any]) -> dict[str, int]:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return {"input": prompt, "output": completion, "total": total}


def _pricing_table_for_cp_key(session: Session, proxy_key_id: str):
    key = session.get(ProxyKey, proxy_key_id)
    if not key or not key.member_id:
        return None
    member = session.get(Member, key.member_id)
    if not member or not member.team_id:
        return None
    return get_cursor_pricing_table(session=session, team_id=member.team_id)


def record_cp_gateway_usage(
    session: Session,
    *,
    proxy_key_id: str,
    credential_id: str | None,
    model: str | None,
    tokens: dict[str, int],
    request_id: str | None = None,
) -> None:
    rid = request_id or uuid.uuid4().hex[:16]
    inp = int(tokens.get("input") or 0)
    out = int(tokens.get("output") or 0)
    total = int(tokens.get("total") or inp + out)
    token_breakdown = {
        "input": inp,
        "output": out,
        "cache_read": 0,
        "cache_write": 0,
        "reasoning": 0,
    }
    table = _pricing_table_for_cp_key(session, proxy_key_id)
    cost_cents = estimate_cost_cents(model, token_breakdown, table=table)
    session.add(
        ProxyKeyUsage(
            proxy_key_id=proxy_key_id,
            credential_id=credential_id,
            request_id=rid,
            model=model,
            tokens_input=inp,
            tokens_output=out,
            total_tokens=total,
            cost_cents=cost_cents,
            ts=utcnow(),
        )
    )
    session.flush()
