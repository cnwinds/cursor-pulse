"""Operational UI visibility for AI accounts (quota board, pool lists, etc.)."""

from __future__ import annotations

from pulse.storage.models import AiAccount


def account_is_active_row(account: AiAccount) -> bool:
    """False when the account was soft-deleted from the ledger."""
    return account.deleted_at is None


def exclude_from_quota_board(sync_blocker: str | None) -> bool:
    """Hide accounts whose primary API Key was deleted (看板「Key已删除」)."""
    return sync_blocker == "key_revoked"
