"""Proxy control-plane facade.

Seams (prefer importing the concrete module in new code):

- **Proxy Authorize** → ``pulse.proxy.authorize``
- **Usage Ledger** (record / window / loan totals) → ``pulse.proxy.usage`` /
  ``pulse.proxy.usage_queries``
- **Proxy Usage Rollup** (by_account / by_model / by_day) → ``pulse.proxy.usage_rollup``
- **Credential Pool Board** → ``pulse.proxy.pool_board``
- **Proxy Key CRUD** → ``pulse.proxy.key_crud``
- Shared clock / windows → ``pulse.proxy.clock``
"""

from __future__ import annotations

from pulse.proxy.authorize import authorize_status
from pulse.proxy.clock import WINDOW_5H, WINDOW_7D, utcnow
from pulse.proxy.key_crud import (
    build_client_command,
    cents_to_usd,
    create_key,
    evaluate_key,
    find_key_by_plaintext,
    key_summary,
    record_event,
    resume_key,
    reveal_plaintext,
    suspend_key,
    usd_to_cents,
)
from pulse.proxy.pool_board import list_pool_credentials, list_pool_ranking_board
from pulse.proxy.usage import (
    canonical_turn_ended_tokens,
    estimate_cost_cents,
    loan_proxy_totals,
    loan_proxy_usage_summary,
    record_usages,
    reprice_proxy_usages,
    total_tokens_from_canonical,
    total_usage,
    window_usage_cost,
    window_usage_tokens,
)

# Alias kept for any lingering ``service._utcnow`` callers.
_utcnow = utcnow

__all__ = [
    "WINDOW_5H",
    "WINDOW_7D",
    "authorize_status",
    "build_client_command",
    "canonical_turn_ended_tokens",
    "cents_to_usd",
    "create_key",
    "estimate_cost_cents",
    "evaluate_key",
    "find_key_by_plaintext",
    "key_summary",
    "list_pool_credentials",
    "list_pool_ranking_board",
    "loan_proxy_totals",
    "loan_proxy_usage_summary",
    "record_event",
    "record_usages",
    "reprice_proxy_usages",
    "resume_key",
    "reveal_plaintext",
    "suspend_key",
    "total_tokens_from_canonical",
    "total_usage",
    "usd_to_cents",
    "utcnow",
    "window_usage_cost",
    "window_usage_tokens",
]
