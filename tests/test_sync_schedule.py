from datetime import datetime, timedelta, timezone

from pulse.config import AppConfig, CollectionConfig, CursorSyncConfig
from pulse.ingestion.sync_schedule import (
    account_jitter_sec,
    accelerate_sync_schedules,
    apply_sync_success,
    backoff_seconds,
    init_schedule_on_bind,
)


def test_account_jitter_is_stable():
    assert account_jitter_sec("acc-1") == account_jitter_sec("acc-1")
    assert 0 <= account_jitter_sec("acc-1") < 3600


def test_backoff_grows_with_retries():
    assert backoff_seconds(1) >= 60
    assert backoff_seconds(3) >= backoff_seconds(1)


def test_init_schedule_on_bind_sets_next_sync():
    from pulse.storage.models import AiAccountCredential

    cred = AiAccountCredential(
        account_id="acc-1",
        vendor_id="v1",
        credential_type="cursor_api_key",
        encrypted_value="x",
        key_hint="crsr",
        bound_by_member_id="m1",
    )
    now = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    init_schedule_on_bind(cred, now=now)
    assert cred.next_sync_at is not None
    assert cred.next_sync_at > now
    assert cred.sync_jitter_sec >= 0


def test_apply_sync_success_uses_interval_minutes():
    from pulse.storage.models import AiAccountCredential

    cred = AiAccountCredential(
        account_id="acc-1",
        vendor_id="v1",
        credential_type="cursor_api_key",
        encrypted_value="x",
        key_hint="crsr",
        bound_by_member_id="m1",
        sync_jitter_sec=0,
    )
    now = datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc)
    config = AppConfig(cursor_sync=CursorSyncConfig(default_interval_minutes=60))
    apply_sync_success(cred, config, now=now)
    assert cred.next_sync_at == now + timedelta(minutes=60)


def test_report_period_previous_month():
    from zoneinfo import ZoneInfo

    from pulse.periods import report_period_for_config

    config = AppConfig(
        collection=CollectionConfig(
            report_period_mode="previous",
            timezone="Asia/Shanghai",
        )
    )
    now = datetime(2026, 7, 15, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert report_period_for_config(config, now) == "2026-06"


def test_accelerate_sync_schedules_pulls_forward_long_next_sync():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from pulse.storage.models import AiAccountCredential, Base

    now = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    cred = AiAccountCredential(
        account_id="acc-1",
        vendor_id="v1",
        credential_type="cursor_api_key",
        encrypted_value="x",
        key_hint="crsr",
        bound_by_member_id="m1",
        status="active",
        sync_enabled=True,
        key_role="primary",
        next_sync_at=now + timedelta(hours=24),
    )
    session.add(cred)
    session.commit()
    config = AppConfig(cursor_sync=CursorSyncConfig(default_interval_minutes=60))
    changed = accelerate_sync_schedules(session, config, now=now)
    assert changed == 1
    session.refresh(cred)
    from pulse.ingestion.sync_schedule import _utc_aware

    assert _utc_aware(cred.next_sync_at) == now
