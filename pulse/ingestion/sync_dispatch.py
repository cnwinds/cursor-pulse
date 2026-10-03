from __future__ import annotations

from pulse.ingestion.coding_plan_sync import CODING_PLAN_VENDORS, CodingPlanQuotaSyncService
from pulse.ingestion.sync import CursorSyncService
from pulse.ingestion.types import IngestionResult
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount


def sync_account_by_vendor(
    session,
    encryption_key: str,
    account: AiAccount,
    account_id: str,
    *,
    channel: str = "scheduler",
    member_id: str | None = None,
    cursor_client: CursorApiClient | None = None,
    app_config=None,
    on_demand_notify=None,
    enforce_on_demand_disabled: bool = True,
) -> IngestionResult:
    slug = account.vendor.slug if account.vendor else ""
    if slug == "cursor":
        return CursorSyncService(
            session,
            encryption_key,
            cursor_client=cursor_client,
            on_demand_notify=on_demand_notify,
            enforce_on_demand_disabled=enforce_on_demand_disabled,
            app_config=app_config,
        ).sync_account(account_id, channel=channel, member_id=member_id)
    if slug in CODING_PLAN_VENDORS:
        return CodingPlanQuotaSyncService(session, encryption_key).sync_account(
            account_id,
            channel=channel,
            member_id=member_id,
        )
    raise ValueError(f"unsupported vendor for sync: {slug}")
