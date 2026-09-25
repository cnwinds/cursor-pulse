"""Usage ledger for Coding Plan OpenAI gateway."""

from __future__ import annotations

import uuid
from typing import Any

from pulse.proxy.clock import utcnow
from pulse.storage.models import ProxyKeyUsage
from sqlalchemy.orm import Session


def parse_openai_usage(body: dict[str, Any]) -> dict[str, int]:
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return {"input": prompt, "output": completion, "total": total}


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
    session.add(
        ProxyKeyUsage(
            proxy_key_id=proxy_key_id,
            credential_id=credential_id,
            request_id=rid,
            model=model,
            tokens_input=inp,
            tokens_output=out,
            total_tokens=total,
            cost_cents=0,
            ts=utcnow(),
        )
    )
    session.flush()
