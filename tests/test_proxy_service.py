from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pulse.proxy import service
from pulse.proxy.keys import generate_proxy_key, hash_proxy_key
from pulse.storage.models import Base, ProxyEvent, ProxyKey, ProxyKeyUsage

NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = sf()
    yield s
    s.close()


def _add_key(session, plaintext: str, **kwargs) -> ProxyKey:
    key = ProxyKey(
        key_hash=hash_proxy_key(plaintext),
        key_hint=plaintext[:11],
        name=kwargs.pop("name", "k"),
        member_id=kwargs.pop("member_id", "m1"),
        mode=kwargs.pop("mode", "unlimited"),
        **kwargs,
    )
    session.add(key)
    session.flush()
    return key


def test_authorize_unknown_key(session):
    result = service.authorize_status(session, "pk_nope", now=NOW)
    assert result["status"] == "invalid"
    assert result["reason"] == "unknown_key"
    assert result["proxy_key_id"] is None


def test_authorize_ok_unlimited(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    result = service.authorize_status(session, plaintext, now=NOW)
    assert result == {
        "status": "ok",
        "proxy_key_id": key.id,
        "mode": "unlimited",
        "reason": None,
        "credential_id": None,
        "loan_id": None,
    }


def test_authorize_revoked_and_expired(session):
    p1, _, _ = generate_proxy_key()
    _add_key(session, p1, status="revoked")
    assert service.authorize_status(session, p1, now=NOW)["reason"] == "revoked"

    p2, _, _ = generate_proxy_key()
    _add_key(session, p2, expires_at=NOW - timedelta(seconds=1))
    assert service.authorize_status(session, p2, now=NOW)["reason"] == "expired"


def test_authorize_suspended(session):
    plaintext, _, _ = generate_proxy_key()
    _add_key(session, plaintext, status="suspended", suspended_reason="token_limit_exceeded")
    result = service.authorize_status(session, plaintext, now=NOW)
    assert result["status"] == "suspended"
    assert result["reason"] == "token_limit_exceeded"


def test_authorize_window_limited(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", window_5h_token_limit=100)
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=60, ts=NOW - timedelta(hours=1))
    )
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=50, ts=NOW - timedelta(hours=2))
    )
    session.flush()
    result = service.authorize_status(session, plaintext, now=NOW)
    assert result["status"] == "window_limited"
    assert result["proxy_key_id"] == key.id


def test_window_ignores_old_usage(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", window_5h_token_limit=100)
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=500, ts=NOW - timedelta(hours=6))
    )
    session.flush()
    assert service.window_usage_tokens(session, key.id, now=NOW) == 0
    assert service.authorize_status(session, plaintext, now=NOW)["status"] == "ok"


def test_window_limit_boundary_exact(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", window_5h_token_limit=100)
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=100, ts=NOW - timedelta(hours=1))
    )
    session.flush()
    assert service.authorize_status(session, plaintext, now=NOW)["status"] == "window_limited"


def test_window_includes_exact_5h_boundary(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", window_5h_token_limit=1000)
    session.add(
        ProxyKeyUsage(proxy_key_id=key.id, total_tokens=10, ts=NOW - timedelta(hours=5))
    )
    session.flush()
    assert service.window_usage_tokens(session, key.id, now=NOW) == 10


def _usage_item(key: ProxyKey, tokens: dict, model: str = "claude-sonnet-4") -> dict:
    return {
        "proxy_key_id": key.id,
        "credential_id": "cred-1",
        "model": model,
        "tokens": tokens,
        "ts": NOW.isoformat(),
    }


def test_record_usage_computes_total_and_cost(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    result = service.record_usages(
        session,
        [_usage_item(key, {"input": 1_000_000, "output": 100_000, "cache_read": 0,
                           "cache_write": 0, "reasoning": 0})],
        now=NOW,
    )
    assert result["recorded"] == 1
    assert result["suspended"] == []
    usage = session.query(ProxyKeyUsage).one()
    assert usage.total_tokens == 1_100_000
    assert usage.cost_cents > 0  # 定价表命中 claude-* glob 规则
    ts = usage.ts
    if ts.tzinfo is None:
        # SQLite 不保留 tzinfo，按 authorize_status 的约定 naive → UTC 归一化后比较
        ts = ts.replace(tzinfo=timezone.utc)
    assert ts == NOW


def test_canonical_turn_ended_derives_no_cache_from_inclusive_input():
    """TurnEnded field1 常为含 cache 的 input 合计；需拆成官方口径的 no_cache。"""
    # 指纹样例：官方 input_no_cache=207, cache_w=140243, cache_r=10584886, out=43745
    raw = {
        "input": 10_725_336,  # == 207 + 140243 + 10584886
        "output": 43_745,
        "cache_read": 10_584_886,
        "cache_write": 140_243,
        "reasoning": 17_629,
    }
    got = service.canonical_turn_ended_tokens(raw)
    assert got["input"] == 207
    assert got["output"] == 43_745
    assert got["cache_read"] == 10_584_886
    assert got["cache_write"] == 140_243
    assert got["reasoning"] == 17_629
    assert service.total_tokens_from_canonical(got) == 10_786_710


def test_canonical_turn_ended_keeps_already_no_cache_input():
    raw = {
        "input": 207,
        "output": 100,
        "cache_read": 10_000,
        "cache_write": 50,
        "reasoning": 0,
    }
    got = service.canonical_turn_ended_tokens(raw)
    assert got["input"] == 207
    assert service.total_tokens_from_canonical(got) == 207 + 100 + 10_000 + 50


def test_record_usage_dedupes_inclusive_input_tokens(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    service.record_usages(
        session,
        [
            _usage_item(
                key,
                {
                    "input": 10_725_336,
                    "output": 43_745,
                    "cache_read": 10_584_886,
                    "cache_write": 140_243,
                    "reasoning": 17_629,
                },
                model="gpt-5.6-sol-max",
            )
        ],
        now=NOW,
    )
    usage = session.query(ProxyKeyUsage).one()
    assert usage.tokens_input == 207
    assert usage.tokens_cache_read == 10_584_886
    assert usage.tokens_cache_write == 140_243
    assert usage.total_tokens == 10_786_710
    # 计价必须按 no_cache=207；estimate_cost_cents 对 inclusive input 也要先 canonical
    expected_cents = service.estimate_cost_cents(
        "gpt-5.6-sol-max",
        {
            "input": 207,
            "output": 43_745,
            "cache_read": 10_584_886,
            "cache_write": 140_243,
            "reasoning": 17_629,
        },
    )
    assert usage.cost_cents == expected_cents
    assert (
        service.estimate_cost_cents(
            "gpt-5.6-sol-max",
            {
                "input": 10_725_336,
                "output": 43_745,
                "cache_read": 10_584_886,
                "cache_write": 140_243,
                "reasoning": 17_629,
            },
        )
        == expected_cents
    )


def test_reprice_proxy_usages_fixes_historical_double_count(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    # 模拟修复前入库：tokens_input 仍是 inclusive raw，total/cost 双计
    session.add(
        ProxyKeyUsage(
            proxy_key_id=key.id,
            model="gpt-5.6-sol-max",
            tokens_input=10_725_336,
            tokens_output=43_745,
            tokens_cache_read=10_584_886,
            tokens_cache_write=140_243,
            tokens_reasoning=17_629,
            total_tokens=21_511_839,
            cost_cents=99_999,
            ts=NOW,
        )
    )
    session.flush()

    result = service.reprice_proxy_usages(session)
    assert result["scanned"] == 1
    assert result["updated"] == 1

    usage = session.query(ProxyKeyUsage).one()
    assert usage.tokens_input == 207
    assert usage.total_tokens == 10_786_710
    assert usage.cost_cents == service.estimate_cost_cents(
        "gpt-5.6-sol-max",
        {
            "input": 207,
            "output": 43_745,
            "cache_read": 10_584_886,
            "cache_write": 140_243,
            "reasoning": 17_629,
        },
    )
    # 幂等
    again = service.reprice_proxy_usages(session)
    assert again["updated"] == 0


def test_record_usage_unknown_key_skipped(session):
    result = service.record_usages(
        session, [{"proxy_key_id": "missing", "tokens": {"input": 1}}], now=NOW
    )
    assert result == {"recorded": 0, "suspended": []}


def test_token_limit_suspends(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", token_limit=100)
    result = service.record_usages(
        session, [_usage_item(key, {"input": 150})], now=NOW
    )
    assert result["suspended"] == [key.id]
    session.refresh(key)
    assert key.status == "suspended"
    assert key.suspended_reason == "token_limit_exceeded"
    event = session.query(ProxyEvent).filter_by(event_type="suspended").one()
    assert event.proxy_key_id == key.id


def test_cost_limit_suspends(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", cost_limit_cents=1)
    result = service.record_usages(
        session, [_usage_item(key, {"input": 1_000_000})], now=NOW
    )
    assert result["suspended"] == [key.id]
    session.refresh(key)
    assert key.suspended_reason == "cost_limit_exceeded"


def test_unlimited_mode_never_suspends(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="unlimited", token_limit=1)
    result = service.record_usages(
        session, [_usage_item(key, {"input": 10_000_000})], now=NOW
    )
    assert result["suspended"] == []
    session.refresh(key)
    assert key.status == "active"


def test_resume_after_raising_limit(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", token_limit=100)
    service.record_usages(session, [_usage_item(key, {"input": 150})], now=NOW)
    session.refresh(key)
    assert service.resume_key(session, key) is False  # 仍超限
    key.token_limit = 1000
    assert service.resume_key(session, key) is True
    assert key.status == "active"
    assert key.suspended_reason is None
    assert session.query(ProxyEvent).filter_by(event_type="resumed").count() == 1


def test_record_event(session):
    service.record_event(
        session, event_type="rotation", credential_id="c1", detail="rate_limit"
    )
    session.flush()
    event = session.query(ProxyEvent).one()
    assert event.event_type == "rotation"
    assert event.credential_id == "c1"


def test_key_summary_fields_and_totals(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", token_limit=10000,
                   window_5h_token_limit=500)
    service.record_usages(
        session,
        [_usage_item(key, {"input": 2000, "output": 1000})],
        now=NOW,
    )
    summary = service.key_summary(session, key, now=NOW)
    assert summary["id"] == key.id
    assert summary["key_hint"] == key.key_hint
    assert summary["name"] == key.name
    assert summary["mode"] == "quota"
    assert summary["token_limit"] == 10000
    assert summary["window_5h_token_limit"] == 500
    assert summary["status"] == "active"
    assert summary["total_tokens"] == 3000
    assert summary["window_5h_tokens"] == 3000
    assert summary["total_cost_cents"] > 0
    assert summary["expires_at"] is None
    assert summary["created_at"] is not None


def test_suspend_is_idempotent(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext, mode="quota", token_limit=100)
    service.record_usages(session, [_usage_item(key, {"input": 150})], now=NOW)
    service.record_usages(session, [_usage_item(key, {"input": 200})], now=NOW)
    session.refresh(key)
    assert key.status == "suspended"
    assert session.query(ProxyEvent).filter_by(event_type="suspended").count() == 1


def test_record_usage_sanitizes_tokens_and_bad_ts(session):
    plaintext, _, _ = generate_proxy_key()
    key = _add_key(session, plaintext)
    result = service.record_usages(
        session,
        [
            {"proxy_key_id": key.id, "tokens": {"input": -50, "output": None}, "ts": "not-a-date"},
            {"proxy_key_id": key.id, "tokens": {"input": -50, "output": None}, "ts": NOW.isoformat()},
        ],
        now=NOW,
    )
    assert result["recorded"] == 1
    usage = session.query(ProxyKeyUsage).one()
    assert usage.tokens_input == 0
    assert usage.tokens_output == 0
    assert usage.total_tokens == 0


def test_loan_proxy_usage_summary_aggregates_by_model_and_window(session):
    loan_id = "loan-agg-1"
    session.add(
        ProxyKeyUsage(
            loan_id=loan_id,
            model="claude-opus-4-8",
            total_tokens=700,
            cost_cents=180,
            ts=NOW - timedelta(days=1),
        )
    )
    session.add(
        ProxyKeyUsage(
            loan_id=loan_id,
            model="composer-2.5-fast",
            total_tokens=190,
            cost_cents=30,
            ts=NOW - timedelta(hours=2),
        )
    )
    session.add(
        ProxyKeyUsage(
            loan_id=loan_id,
            model="claude-opus-4-8",
            total_tokens=100,
            cost_cents=20,
            ts=NOW - timedelta(days=10),
        )
    )
    session.add(
        ProxyKeyUsage(
            loan_id="other-loan",
            model="ignored",
            total_tokens=999,
            cost_cents=99,
            ts=NOW,
        )
    )
    session.flush()

    full = service.loan_proxy_usage_summary(session, loan_id)
    assert full["request_count"] == 3
    assert full["total_tokens"] == 990
    assert full["cost_cents"] == 230
    assert full["cost_usd"] == pytest.approx(2.3)
    assert [m["model"] for m in full["models"]] == [
        "claude-opus-4-8",
        "composer-2.5-fast",
    ]
    assert full["models"][0]["events"] == 2
    assert full["models"][0]["tokens"] == 800

    windowed = service.loan_proxy_usage_summary(
        session,
        loan_id,
        start=NOW - timedelta(days=2),
        end=NOW,
    )
    assert windowed["request_count"] == 2
    assert windowed["total_tokens"] == 890
    assert windowed["cost_cents"] == 210


def test_loan_proxy_usage_summary_empty(session):
    summary = service.loan_proxy_usage_summary(session, "missing-loan")
    assert summary["request_count"] == 0
    assert summary["total_tokens"] == 0
    assert summary["models"] == []
    assert summary["data_updated_at"] is None
