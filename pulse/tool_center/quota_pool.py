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

    BYOK / self-hosted third-party models do not consume an included bucket, so
    they resolve to ``unknown`` — selection then requires both buckets to have
    Snapshot Headroom, matching Go ``snapshotQuotaOK``.
    """
    if not (model or "").strip():
        return "unknown"
    if is_auto_composer_model(model):
        return "auto"
    if is_likely_byok_model(model):
        return "unknown"
    return "api"
