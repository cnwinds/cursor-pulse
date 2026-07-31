"""Snapshot Headroom rules shared by Credential Pool Intake and Go proxy."""

from __future__ import annotations

from typing import Literal

QuotaPoolKind = Literal["auto", "api", "unknown"]


def pct_quota_ok(pct: float | None) -> bool:
    """Snapshot headroom for one Quota Pool.

    Matches Go ``pctQuotaOK``: missing pct is unknown → treat as OK.
    """
    if pct is None:
        return True
    return pct < 100


def snapshot_has_any_pool_headroom(
    *,
    auto_pct: float | None,
    api_pct: float | None,
) -> bool:
    """Credential Pool Intake: at least one Quota Pool still has snapshot headroom.

    Intentionally OR (not AND). Request-time selection for an unknown pool
    still requires both buckets OK — see snapshot_quota_ok_for_pool.
    """
    return pct_quota_ok(auto_pct) or pct_quota_ok(api_pct)


def snapshot_quota_ok_for_pool(
    pool: QuotaPoolKind | str,
    *,
    auto_pct: float | None,
    api_pct: float | None,
) -> bool:
    """Match Go ``snapshotQuotaOK`` for a Quota Pool kind."""
    if pool == "auto":
        return pct_quota_ok(auto_pct)
    if pool == "api":
        return pct_quota_ok(api_pct)
    return pct_quota_ok(auto_pct) and pct_quota_ok(api_pct)
