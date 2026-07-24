from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.ingestion.adapters.cursor_api import CursorApiAdapter
from pulse.ingestion.credentials import CredentialService, _apply_key_account_identifier, _ledger_identifier
from pulse.ingestion.on_demand import (
    OnDemandEnforceResult,
    enforce_on_demand_disabled,
)
from pulse.ingestion.service import UsageIngestionService
from pulse.ingestion.sync_errors import classify_sync_error
from pulse.ingestion.types import IngestionContext, IngestionResult
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AccountQuotaSnapshot, AiAccount, UsageSummary
from pulse.tool_center.repository import ToolCenterRepository

logger = logging.getLogger(__name__)

OnDemandNotify = Callable[[AiAccount, OnDemandEnforceResult], None]


def _recompute_account_summaries(
    session: Session, team_id: str, account_id: str
) -> None:
    repo = ToolCenterRepository(session, team_id)
    periods = session.scalars(
        select(UsageSummary.period).where(UsageSummary.account_id == account_id)
    ).all()
    for period in periods:
        repo.recompute_usage_summary(account_id, period)


def _ms_to_date(ms: str | int) -> date:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date()


def _apply_period_usage(
    session: Session,
    account: AiAccount,
    period_usage: dict,
    *,
    captured_at: datetime | None = None,
) -> AccountQuotaSnapshot | None:
    plan_usage = period_usage.get("planUsage")
    if not plan_usage:
        return None

    cycle_start = _ms_to_date(period_usage["billingCycleStart"])
    cycle_end = _ms_to_date(period_usage["billingCycleEnd"])

    if account.resets_on_source != "manual-locked":
        account.usage_resets_on = cycle_end
        account.resets_on_source = "api"

    snapshot = AccountQuotaSnapshot(
        account_id=account.id,
        captured_at=captured_at or datetime.now(timezone.utc),
        cycle_start=cycle_start,
        cycle_end=cycle_end,
        limit_cents=int(plan_usage.get("limit") or 0),
        used_cents=int(plan_usage.get("totalSpend") or 0),
        remaining_cents=int(plan_usage.get("remaining") or 0),
        auto_pct=float(plan_usage["autoPercentUsed"]) if plan_usage.get("autoPercentUsed") is not None else None,
        api_pct=float(plan_usage["apiPercentUsed"]) if plan_usage.get("apiPercentUsed") is not None else None,
        total_pct=float(plan_usage["totalPercentUsed"]) if plan_usage.get("totalPercentUsed") is not None else None,
    )
    session.add(snapshot)
    return snapshot


class CursorSyncService:
    def __init__(
        self,
        session: Session,
        encryption_key: str,
        *,
        cursor_client: CursorApiClient | None = None,
        on_demand_notify: OnDemandNotify | None = None,
    ):
        self.session = session
        self.encryption_key = encryption_key
        self.cursor_client = cursor_client or CursorApiClient()
        self.on_demand_notify = on_demand_notify
        self.credential_service = CredentialService(
            session, encryption_key, cursor_client=self.cursor_client
        )

    def _enforce_on_demand(self, account: AiAccount, token: str, api_key: str) -> None:
        result = enforce_on_demand_disabled(
            self.cursor_client, token, api_key=api_key
        )
        if result.status == "already_disabled":
            return
        if result.status == "disabled_now":
            logger.warning(
                "on-demand spending disabled for account %s (%s)",
                account.id,
                account.account_identifier,
            )
        elif result.status == "disable_failed":
            logger.error(
                "failed to disable on-demand for account %s: %s",
                account.id,
                result.error,
            )
        else:
            logger.warning(
                "on-demand check failed for account %s: %s",
                account.id,
                result.error,
            )
        if result.status in ("disabled_now", "disable_failed") and self.on_demand_notify:
            try:
                self.on_demand_notify(account, result)
            except Exception:
                logger.exception(
                    "on-demand notify failed for account %s", account.id
                )

    def sync_account(
        self, account_id: str, *, channel: str = "scheduler"
    ) -> IngestionResult:
        cred = self.credential_service.get_primary_credential(account_id)
        if not cred or not cred.sync_enabled:
            raise ValueError("no active credential")

        account = self.session.scalar(
            select(AiAccount)
            .options(joinedload(AiAccount.vendor))
            .where(AiAccount.id == account_id)
        )
        if not account or not account.vendor:
            raise ValueError("account not found")

        team_id = account.team_id
        if not team_id:
            raise ValueError("account has no team_id")

        try:
            api_key = self.credential_service.decrypt_api_key(cred)
            token = self.cursor_client.get_access_token(api_key)
            if not _ledger_identifier(account):
                key_email = self.cursor_client.resolve_api_key_account_email(api_key)
                if key_email:
                    _apply_key_account_identifier(account, key_email)
            self._enforce_on_demand(account, token, api_key)
            period_usage = self.cursor_client.get_current_period_usage(
                token, api_key=api_key
            )

            now = datetime.now(timezone.utc)
            end_ms = int(now.timestamp() * 1000)
            start_ms = int(period_usage["billingCycleStart"])

            events = list(
                self.cursor_client.iter_filtered_usage_events(
                    token, start_ms=start_ms, end_ms=end_ms, api_key=api_key
                )
            )

            by_period: dict[str, list] = defaultdict(list)
            for event in events:
                period = event.event_date.strftime("%Y-%m")
                by_period[period].append(event)

            ingestion_service = UsageIngestionService(self.session, team_id)
            adapter = CursorApiAdapter()
            results: list[IngestionResult] = []

            for billing_period in sorted(by_period):
                period_events = by_period[billing_period]
                context = IngestionContext(
                    account_id=account_id,
                    vendor_id=account.vendor_id,
                    vendor_slug=account.vendor.slug,
                    billing_period=billing_period,
                    member_id=None,
                    channel=channel,
                    source_type="api_sync",
                    triggered_by="system",
                    events=period_events,
                    metadata={
                        "sync_source": "cursor_api",
                        "period_usage": period_usage,
                    },
                )
                results.append(
                    ingestion_service.ingest(
                        context=context,
                        adapter=adapter,
                        status="confirmed",
                        commit=False,
                    )
                )

            _apply_period_usage(self.session, account, period_usage, captured_at=now)

            cred.last_sync_at = now
            cred.last_sync_status = "success"
            cred.last_sync_error = None
            _recompute_account_summaries(self.session, team_id, account_id)
            self.session.commit()

            if results:
                total_events = sum(r.event_count for r in results)
                last = results[-1]
                return IngestionResult(
                    ingestion_id=last.ingestion_id,
                    event_count=total_events,
                    status=last.status,
                )
            return IngestionResult(ingestion_id="", event_count=0, status="confirmed")

        except Exception as exc:
            classified = classify_sync_error(exc)
            self.session.rollback()
            cred = self.credential_service.get_primary_credential(account_id)
            if cred:
                cred.last_sync_status = "failed"
                cred.last_sync_error = str(classified)
                self.session.commit()
            raise classified from exc
