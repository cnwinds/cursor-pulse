from datetime import datetime, timezone

import pytest

from pulse.domain import CostRaw, UsageEventRecord
from pulse.pricing.billing_scope import (
    KIND_FAMILY_LABELS,
    classify_billing_scope,
    is_cursor_billable_scope,
    is_likely_byok_model,
    kind_family,
)
from pulse.pricing.estimator import aggregate_cursor_billing, estimate_event_record, resolve_cost_fields
from pulse.storage.models import UsageRecord


def test_classify_billing_scope():
    assert classify_billing_scope(kind="User API Key", model="GLM-5.1") == "external"
    assert classify_billing_scope(kind="USAGE_EVENT_KIND_USER_API_KEY", model="GLM-5.2") == "external"
    assert classify_billing_scope(kind="Errored, No Charge", model="auto") == "excluded"
    assert classify_billing_scope(kind="USAGE_EVENT_KIND_ERRORED_NOT_CHARGED", model="GLM-5.2") == (
        "excluded"
    )
    assert classify_billing_scope(kind="Free", model="composer-2.5") == "excluded"
    assert classify_billing_scope(kind="USAGE_EVENT_KIND_FREE_CREDIT", model="default") == "excluded"
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
    assert classify_billing_scope(kind="USAGE_EVENT_KIND_INCLUDED_IN_PRO", model="composer-2.5") == (
        "auto_composer"
    )
    # Cursor catalog GLM is INCLUDED named/API, not BYOK.
    assert classify_billing_scope(kind="Included", model="glm-5.2-high") == "api"
    assert classify_billing_scope(
        kind="USAGE_EVENT_KIND_INCLUDED_IN_PRO", model="glm-5.2-high"
    ) == "api"
    assert classify_billing_scope(kind="Included", model="GLM-5.2") == "api"
    assert classify_billing_scope(kind="Included", model="MiniMax-Text-01") == "api"
    assert classify_billing_scope(kind="Included", model="premium") == "api"
    assert classify_billing_scope(kind="Included", model="Premium (Codex 5.3)") == "api"
    # Missing kind: PascalCase BYOK heuristic; INCLUDED + PascalCase stays api.
    assert classify_billing_scope(kind="", model="GLM-5.2") == "external"
    assert classify_billing_scope(kind=None, model="MiniMax-M2.7") == "external"
    assert classify_billing_scope(kind="", model="glm-5.2-high") == "api"
    assert classify_billing_scope(kind=None, model="claude-4-sonnet") == "api"


def test_kind_family_labels_cover_unknown():
    assert KIND_FAMILY_LABELS["unknown"] == "未知"
    assert KIND_FAMILY_LABELS["included"] == "套餐"
    assert KIND_FAMILY_LABELS["user_api_key"] == "BYOK"
    assert KIND_FAMILY_LABELS["excluded"] == "未计费"


def test_is_cursor_billable_scope_only_auto_and_api():
    assert is_cursor_billable_scope("auto_composer") is True
    assert is_cursor_billable_scope("api") is True
    assert is_cursor_billable_scope("external") is False
    assert is_cursor_billable_scope("excluded") is False
    assert is_cursor_billable_scope("third_party") is False


def test_kind_family_collapses_included_variants():
    assert kind_family("USAGE_EVENT_KIND_INCLUDED_IN_PRO") == "included"
    assert kind_family("USAGE_EVENT_KIND_INCLUDED_IN_PRO_PLUS") == "included"
    assert kind_family("Included") == "included"
    assert kind_family("USAGE_EVENT_KIND_USER_API_KEY") == "user_api_key"
    assert kind_family("User API Key") == "user_api_key"
    assert kind_family("USAGE_EVENT_KIND_ERRORED_NOT_CHARGED") == "excluded"
    assert kind_family("Free") == "excluded"
    assert kind_family("") == "unknown"
    assert kind_family(None) == "unknown"
    assert kind_family("USAGE_BASED") == "unknown"
    assert kind_family("USAGE_EVENT_KIND_USAGE_BASED") == "unknown"


def test_is_likely_byok_model_uses_casing_heuristic():
    assert is_likely_byok_model("GLM-5.2") is True
    assert is_likely_byok_model("MiniMax-M2.7") is True
    assert is_likely_byok_model("glm-5.2-high") is False
    assert is_likely_byok_model("deepseek-reasoner") is False
    assert is_likely_byok_model("claude-4-sonnet") is False
    assert is_likely_byok_model("composer-2.5") is False


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
    proto = resolve_cost_fields(
        _included_record(
            kind="USAGE_EVENT_KIND_USER_API_KEY", model="GLM-5.2", cost_raw=CostRaw.NONE
        )
    )
    assert proto["cost_basis"] == "external"


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


def test_aggregate_cursor_billing_included_glm_counts_as_api():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="USAGE_EVENT_KIND_INCLUDED_IN_PRO",
            model="glm-5.2-high",
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
    assert billing["cursor_pools"]["third_party"]["spend_usd"] == pytest.approx(0.0)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(8.65)
    assert "glm-5.2-high" in billing["cursor_pools"]["api"]["breakdown_by_model"]
    assert "glm-5.2-high" not in billing["cursor_pools"]["third_party"]["breakdown_by_model"]


def test_aggregate_cursor_billing_byok_glm_is_external():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="USAGE_EVENT_KIND_USER_API_KEY",
            model="GLM-5.2",
            max_mode=False,
            tokens_total=500_000,
            cost_raw=CostRaw.NONE.value,
            cost_usd=0,
            cost_basis="external",
        ),
    ]
    billing = aggregate_cursor_billing(records)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(0.0)
    assert billing["cursor_pools"]["third_party"]["spend_usd"] == pytest.approx(0.0)
    assert billing["external_models"]["GLM-5.2"]["total_tokens"] == 500_000


def test_aggregate_cursor_billing_missing_kind_pascalcase_glm_is_external():
    records = [
        UsageRecord(
            ingestion_id="s1",
            member_id="m1",
            event_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            event_date=datetime(2026, 6, 1).date(),
            kind="",
            model="GLM-5.2",
            max_mode=False,
            tokens_total=400_000,
            cost_raw=CostRaw.NONE.value,
            cost_usd=0,
            cost_basis="none",
        ),
    ]
    billing = aggregate_cursor_billing(records)
    assert billing["cursor_pools"]["api"]["spend_usd"] == pytest.approx(0.0)
    assert billing["external_models"]["GLM-5.2"]["total_tokens"] == 400_000
