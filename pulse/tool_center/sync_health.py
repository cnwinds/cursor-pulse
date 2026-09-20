"""同步健康判定：配额看板与出借候选共用同一口径。

判据原本只长在配额看板里（``quota_api._sync_blocker``），看板把结果直接显示为
账号 status。出借要求同一件事：出借账号的 primary 凭证状态正常、且最近一次同步
成功——否则 Pulse 既打不了分，也统计不到借用量。因此把它抽到这里，两边共用。

与 :mod:`pulse.tool_center.ingestion_status` 的 ``ingestion_state`` 区分：后者是
「用量提交流程」视角（含 36 小时滞后与人工提交状态，且把 ``never`` 视为已同步），
本模块只回答「这把凭证现在能不能正常同步」。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.storage.models import AiAccountCredential

# 连续重试到这个次数仍没成功，视为同步失败（与看板原判据一致）
ABNORMAL_SYNC_RETRY_COUNT = 3

SYNC_BLOCKER_STATUSES = frozenset(
    {"no_credential", "key_revoked", "sync_failed", "unsynced"}
)


def credential_sync_blocker(cred: AiAccountCredential | None) -> str | None:
    """primary 凭证的同步阻断原因；``None`` 表示同步正常。"""
    if cred is None:
        return "no_credential"
    if cred.status != "active":
        return "key_revoked"
    if (
        cred.last_sync_status == "failed"
        or int(cred.retry_count or 0) >= ABNORMAL_SYNC_RETRY_COUNT
    ):
        return "sync_failed"
    if cred.last_sync_status != "success":
        return "unsynced"
    return None


def primary_credentials_by_account(
    session: Session, account_ids: list[str]
) -> dict[str, AiAccountCredential]:
    """账号 id → primary 凭证；同账号多把时优先 active 的那把。"""
    if not account_ids:
        return {}
    rows = session.scalars(
        select(AiAccountCredential).where(
            AiAccountCredential.account_id.in_(account_ids),
            AiAccountCredential.key_role == "primary",
        )
    ).all()
    picked: dict[str, AiAccountCredential] = {}
    for row in rows:
        existing = picked.get(row.account_id)
        if existing is None or (existing.status != "active" and row.status == "active"):
            picked[row.account_id] = row
    return picked


def sync_blockers_by_account(
    session: Session, account_ids: list[str]
) -> dict[str, str]:
    """账号 id → 同步阻断原因；只含同步不正常的账号。"""
    creds = primary_credentials_by_account(session, account_ids)
    blockers: dict[str, str] = {}
    for account_id in account_ids:
        reason = credential_sync_blocker(creds.get(account_id))
        if reason:
            blockers[account_id] = reason
    return blockers
