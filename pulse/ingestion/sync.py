from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from pulse.ingestion.adapters.cursor_api import CursorApiAdapter
from pulse.ingestion.credentials import CredentialService
from pulse.ingestion.service import UsageIngestionService
from pulse.ingestion.types import IngestionContext, IngestionResult
from pulse.integrations.cursor_api import CursorApiClient
from pulse.storage.models import AiAccount, AiAccountCredential


class CursorSyncService:
    def __init__(
        self,
        session: Session,
        encryption_key: str,
        *,
        cursor_client: CursorApiClient | None = None,
    ):
        self.session = session
        self.encryption_key = encryption_key
        self.cursor_client = cursor_client or CursorApiClient()
        self.credential_service = CredentialService(
            session, encryption_key, cursor_client=self.cursor_client
        )

    def sync_account(
        self, account_id: str, *, channel: str = "scheduler"
    ) -> IngestionResult:
        cred = self.session.scalar(
            select(AiAccountCredential).where(
                AiAccountCredential.account_id == account_id,
                AiAccountCredential.status == "active",
                AiAccountCredential.sync_enabled.is_(True),
            )
        )
        if not cred:
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
            token = self.cursor_client.exchange_api_key(api_key)
            period_usage = self.cursor_client.get_current_period_usage(token)

            now = datetime.now(timezone.utc)
            end_ms = int(now.timestamp() * 1000)
            if cred.last_sync_at is None:
                start_ms = int(period_usage["billingCycleStart"])
            else:
                start_ms = int(cred.last_sync_at.timestamp() * 1000)

            events = list(
                self.cursor_client.iter_filtered_usage_events(
                    token, start_ms=start_ms, end_ms=end_ms
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

            cred.last_sync_at = now
            cred.last_sync_status = "success"
            cred.last_sync_error = None
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
            self.session.rollback()
            cred = self.session.scalar(
                select(AiAccountCredential).where(
                    AiAccountCredential.account_id == account_id
                )
            )
            if cred:
                cred.last_sync_status = "failed"
                cred.last_sync_error = str(exc)
                self.session.commit()
            raise
