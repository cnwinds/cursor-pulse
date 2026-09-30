"""Quota Pool resolution from a billed model name.

Mirrors Go ``quotaPoolForModel`` (proxy/billing_pool.go) so web-side scoring and
request-time sticky selection agree on which bucket a model draws from. The
model heuristics themselves live in ``pulse.pricing.billing_scope`` — the same
source Go's ``isAutoComposerModel`` / ``isLikelyByokModel`` are kept in sync with.
"""

from __future__ import annotations

from pulse.pricing.billing_scope import is_auto_composer_model, is_likely_byok_model
from pulse.tool_center.snapshot_headroom import QuotaPoolKind


def quota_pool_for_model(model: str | None) -> QuotaPoolKind:
    """Map a billed model to the ``auto`` vs ``api`` Quota Pool.

    BYOK / self-hosted third-party models count as ``auto``, same as Go. An empty
    model only reaches here from loan issuance without a target model; it stays
    ``unknown`` (both buckets must have headroom). Go treats a missing model as
    ``auto`` and never sends ``unknown``.
    """
    if not (model or "").strip():
        return "unknown"
    if is_auto_composer_model(model) or is_likely_byok_model(model):
        return "auto"
    return "api"
