from datetime import datetime, timezone

import pytest

from pulse.domain import CostRaw, UsageEventRecord
from pulse.pricing.billing_scope import classify_billing_scope
from pulse.pricing.estimator import aggregate_cursor_billing, estimate_event_record, resolve_cost_fields
from pulse.storage.models import UsageRecord


def test_classify_billing_scope():
    assert classify_billing_scope(kind="User API Key", model="GLM-5.1") == "external"
    assert classify_billing_scope(kind="Errored, No Charge", model="auto") == "excluded"
    assert classify_billing_scope(kind="Included", model="auto") == "auto_composer"
    assert classify_billing_scope(kind="Included", model="composer-2.5") == "auto_composer"
    assert classify_billing_scope(kind="Included", model="composer-2.6") == "auto_composer"
    assert classify_billing_scope(kind="Included", model="grok-4.5-high") == "auto_composer"
    assert classify_billing_scope(kind="Included", model="cursor-grok-4.5-high") == "auto_composer"
    assert (
        classify_billing_scope(kind="Included", model="cursor-grok-4.5-high-fast")
        == "auto_composer"
    )
    assert classify_billing_scope(kind="Included", model="grok-4.5-fast-high") == "auto_composer"
    assert classify_billing_scope(kind="Included", model="default") == "auto_composer"
    assert classify_billing_scope(kind="Included", model="GLM-5.2") == "third_party"
    assert classify_billing_scope(kind="Included", model="MiniMax-Text-01") == "third_party"
    assert classify_billing_scope(kind="Included", model="premium") == "api"
    assert classify_billing_scope(kind="Included", model="Premium (Codex 5.3)") == "api"


def _included_record(**kwargs) -> UsageEventRecord:
    defaults = dict(
        event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        event_date=datetime(2026, 6, 1).date(),
        kind="Included",
        model="auto",
        max_mode=False,
        tokens_input_cache_write=0,
        tokens_input_no_cache=1_000_000,
        tokens_cache_read=2_000_000,
        tokens_output=100_000,
        tokens_total=3_100_000,
        cost_raw=CostRaw.INCLUDED,
        cost_usd=0,
        cloud_agent_id=None,
        automation_id=None,
        source_row_hash="hash1",
    )
    defaults.update(kwargs)
    return UsageEventRecord(**defaults)


def test_included_premium_uses_api_pool_rates():
    base_tokens = dict(
        tokens_input_cache_write=0,
        tokens_input_no_cache=1_000_000,
        tokens_cache_read=2_000_000,
        tokens_output=100_000,
        tokens_total=3_100_000,
    )
    auto_est = estimate_event_record(_included_record(model="auto", **base_tokens))
    premium_est = estimate_event_record(_included_record(model="premium", **base_tokens))
    assert auto_est is not None and premium_est is not None
    assert premium_est.cost_usd != auto_est.cost_usd
    assert premium_est.pricing_rule == "included:api"
    assert auto_est.pricing_rule == "included:auto_composer"


def test_user_api_key_rows_are_external_basis():
    fields = resolve_cost_fields(
        _included_record(kind="User API Key", model="GLM-5.1", cost_raw=CostRaw.NONE)
    )
    assert fields["cost_basis"] == "external"
    assert fields["cost_usd"] == 0.0


def test_aggregate_cursor_billing_splits_pools_and_external():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="Included",
            model="composer-2.5",
            max_mode=False,
            tokens_total=1000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=5.0,
            cost_basis="estimated",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="Included",
            model="premium",
            max_mode=False,
            tokens_total=2000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=8.0,
            cost_basis="estimated",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="User API Key",
            model="GLM-5.1",
            max_mode=False,
            tokens_total=500_000,
            cost_raw=CostRaw.NONE.value,
            cost_usd=0,
            cost_basis="external",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="Errored, No Charge",
            model="auto",
            max_mode=False,
            tokens_total=10,
            cost_raw=CostRaw.NONE.value,
            cost_usd=0,
            cost_basis="excluded",
        ),
    ]
    billing = aggregate_cursor_billing(records)
    assert billing["cursor_pools"]["auto_composer"]["spend_usd"] == pytest.approx(5.0)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(8.0)
    assert billing["cursor_pools"]["auto_composer"]["tokens_by_model"]["composer-2.5"] == 1000
    assert billing["cursor_pools"]["api"]["tokens_by_model"]["premium"] == 2000
    assert billing["pool_spend_usd"] == pytest.approx(13.0)
    assert billing["external_models"]["GLM-5.1"]["total_tokens"] == 500_000
    assert billing["excluded_event_count"] == 1


def test_aggregate_cursor_billing_grok_in_auto_composer_pool():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="Included",
            model="grok-4.5-high",
            max_mode=False,
            tokens_total=1000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=2.64,
            cost_basis="estimated",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 2).date(),
            kind="Included",
            model="claude-opus-4-8-thinking-high",
            max_mode=False,
            tokens_total=2000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=8.0,
            cost_basis="estimated",
        ),
    ]
    billing = aggregate_cursor_billing(records)
    assert billing["cursor_pools"]["auto_composer"]["spend_usd"] == pytest.approx(2.64)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(8.0)
    assert "grok-4.5-high" in billing["cursor_pools"]["auto_composer"]["breakdown_by_model"]
    assert "grok-4.5-high" not in billing["cursor_pools"]["api"]["breakdown_by_model"]


def test_aggregate_cursor_billing_cursor_prefixed_grok_in_auto_composer_pool():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="Included",
            model="cursor-grok-4.5-high",
            max_mode=False,
            tokens_total=1000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=6.95,
            cost_basis="estimated",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 2).date(),
            kind="Included",
            model="cursor-grok-4.5-high-fast",
            max_mode=False,
            tokens_total=500,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=0.06,
            cost_basis="estimated",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 3, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 3).date(),
            kind="Included",
            model="claude-opus-4-8-thinking-high",
            max_mode=False,
            tokens_total=2000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=8.0,
            cost_basis="estimated",
        ),
    ]
    billing = aggregate_cursor_billing(records)
    assert billing["cursor_pools"]["auto_composer"]["spend_usd"] == pytest.approx(7.01)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(8.0)
    assert "cursor-grok-4.5-high" in billing["cursor_pools"]["auto_composer"]["breakdown_by_model"]
    assert (
        "cursor-grok-4.5-high-fast"
        in billing["cursor_pools"]["auto_composer"]["breakdown_by_model"]
    )
    assert "cursor-grok-4.5-high" not in billing["cursor_pools"]["api"]["breakdown_by_model"]


def test_aggregate_cursor_billing_splits_third_party_models():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="Included",
            model="GLM-5.2",
            max_mode=False,
            tokens_total=1000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=0.85,
            cost_basis="estimated",
        ),
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 2).date(),
            kind="Included",
            model="claude-opus-4-8-thinking-high",
            max_mode=False,
            tokens_total=2000,
            cost_raw=CostRaw.INCLUDED.value,
            cost_usd=0,
            cost_estimated_usd=7.8,
            cost_basis="estimated",
        ),
    ]
    billing = aggregate_cursor_billing(records)
    assert billing["cursor_pools"]["third_party"]["spend_usd"] == pytest.approx(0.85)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(7.8)
    assert "GLM-5.2" in billing["cursor_pools"]["third_party"]["breakdown_by_model"]
    assert "GLM-5.2" not in billing["cursor_pools"]["api"]["breakdown_by_model"]
