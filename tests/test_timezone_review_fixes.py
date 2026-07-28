from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from assistant_platform.contracts.provider import CapabilityInvokeResult
from pulse.capabilities.handlers.quota_self_read import _format_user_message
from pulse.channels.capability_bridge import format_capability_reply
from pulse.tool_center.usage_self import format_usage_self_message
from pulse.util.datetime_fmt import format_data_updated_line
from pulse.util.timezone_ctx import activate_display_timezone


def test_format_capability_reply_respects_display_timezone_context():
    captured = datetime(2026, 7, 15, 4, 30, 0, tzinfo=timezone.utc)
    accounts = [
        {
            "account_id": "a1",
            "account_identifier": "quota@example.com",
            "has_snapshot": True,
            "status": "healthy",
            "total_pct": 10.0,
            "remaining_headroom_pct": 90.0,
            "captured_at": captured.isoformat(),
        }
    ]
    with activate_display_timezone("America/New_York"):
        expected = _format_user_message(accounts)
    result = CapabilityInvokeResult(
        status="succeeded",
        user_message="",
        result={"schema_version": 1, "accounts": accounts},
    )
    with activate_display_timezone("America/New_York"):
        assert format_capability_reply(result) == expected
        assert "00:30:00" in format_data_updated_line(captured)


def test_loan_section_skips_data_updated_when_missing():
    msg = format_usage_self_message(
        mode="billing_cycle",
        period="2026-07",
        accounts=[
            {
                "kind": "loan",
                "identifier": "借用 Key",
                "is_loan": True,
                "usage_source": "quota_approx",
                "loan": {
                    "borrowed_quota_pct": 8.0,
                    "remaining_headroom_pct": 40.0,
                },
            }
        ],
    )
    assert "数据最后更新" not in msg


def test_resolve_display_timezone_uses_configured_session_factory(monkeypatch):
    from pulse.util import timezone_ctx as tz_mod

    tz_mod.invalidate_display_timezone_cache()
    tz_mod._session_factory = None
    tz_mod._base_config = None

    calls: list[str] = []

    def fake_init_db(_url):
        calls.append("init_db")
        return MagicMock()

    monkeypatch.setattr("pulse.storage.db.init_db", fake_init_db)

    config = MagicMock()
    config.collection.timezone = "Asia/Shanghai"
    config.tenant.slug = "default"

    session = MagicMock()
    factory = MagicMock(return_value=session)
    runtime = MagicMock()
    runtime.collection.timezone = "Asia/Tokyo"

    monkeypatch.setattr(
        "pulse.settings.team_store.effective_config_for_tenant",
        lambda _session, _config: runtime,
    )

    tz_mod.configure_display_timezone_resolver(config, factory)
    assert tz_mod.resolve_display_timezone_name() == "Asia/Tokyo"
    assert tz_mod.resolve_display_timezone_name() == "Asia/Tokyo"
    assert calls == []
    assert factory.call_count == 1

    tz_mod.invalidate_display_timezone_cache()
    assert tz_mod.resolve_display_timezone_name() == "Asia/Tokyo"
    assert factory.call_count == 2

    tz_mod.invalidate_display_timezone_cache()
    tz_mod._session_factory = None
    tz_mod._base_config = None
