from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from pulse.tool_center import key_loan_ops


def test_read_self_loan_proxy_summary_hides_lender(monkeypatch):
    monkeypatch.setattr(
        key_loan_ops,
        "build_self_loan_payload",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "empty_reason": None,
            "loans": [
                {
                    "created_at": "2026-07-20T10:12:00+08:00",
                    "usage_source": "proxy",
                    "proxy_request_count": 18,
                    "proxy_total_tokens": 890000,
                    "proxy_cost_usd": 2.1,
                    "remaining_headroom_pct": 12.3,
                    "approx_borrowed_usd": 9.99,
                    "auto_revoke_on_reset": True,
                    "loan_expires_on": "2026-08-01",
                    "api_key": "pka_testkey",
                    "requires_proxy": True,
                    "lender_name": "Alice",
                    "source_identifier": "alice@corp.com",
                }
            ],
        },
    )
    msg = key_loan_ops.read_self_loan(MagicMock(), SimpleNamespace(), SimpleNamespace())
    assert "Alice" not in msg
    assert "alice@corp.com" not in msg
    assert "借出人" not in msg
    assert "Proxy 精确计量" in msg
    assert "18" in msg and "890,000" in msg
    assert "≈$2.10" in msg
    assert "还能用：12.3%" in msg
    assert "近似消耗" not in msg
    assert "pka_testkey" in msg


def test_read_self_loan_falls_back_to_approx(monkeypatch):
    monkeypatch.setattr(
        key_loan_ops,
        "build_self_loan_payload",
        lambda *args, **kwargs: {
            "schema_version": 1,
            "empty_reason": None,
            "loans": [
                {
                    "created_at": "2026-07-20T10:12:00+08:00",
                    "usage_source": "quota_approx",
                    "proxy_request_count": 0,
                    "proxy_total_tokens": 0,
                    "proxy_cost_usd": 0.0,
                    "approx_borrowed_usd": 1.25,
                    "auto_revoke_on_reset": False,
                    "loan_expires_on": None,
                    "api_key": None,
                    "api_key_unavailable": True,
                    "lender_name": "Bob",
                }
            ],
        },
    )
    msg = key_loan_ops.read_self_loan(MagicMock(), SimpleNamespace(), SimpleNamespace())
    assert "Bob" not in msg
    assert "近似消耗：$1.25" in msg
    assert "Proxy" not in msg
