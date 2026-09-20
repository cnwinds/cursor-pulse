"""Auto Lender 发放选号与审计（换号已改由代理在会话内完成，见 test_web_internal_proxy）。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.auto_lender import reset_auto_lender_state
from pulse.tool_center.key_loan_auto import (
    record_auto_lender_decision,
    resolve_auto_lender,
)
from pulse.tool_center.key_loan_delivery import (
    LENDER_MODE_AUTO,
    LENDER_MODE_MANUAL,
    VALID_LENDER_MODES,
)
from tests.conftest import make_team_repo, make_test_session_factory

NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _snapshot(account_id: str, total_pct: float) -> AccountQuotaSnapshot:
    return AccountQuotaSnapshot(
        account_id=account_id,
        captured_at=NOW,
        cycle_start=date(2026, 7, 1),
        cycle_end=date(2026, 8, 1),
        limit_cents=7000,
        used_cents=int(7000 * total_pct / 100),
        remaining_cents=7000 - int(7000 * total_pct / 100),
        total_pct=total_pct,
        auto_pct=total_pct,
        api_pct=total_pct,
    )


@pytest.fixture
def env():
    """最小可用的账号台账：2 个 Cursor 账号 + 快照 + 借用人。"""
    from pulse.storage.models import (
        AiAccount,
        AiAccountCredential,
        AiPlan,
        AiVendor,
        Member,
    )

    sf = make_test_session_factory()
    session = sf()
    team, _ = make_team_repo(session)
    vendor = AiVendor(slug="cursor", name="Cursor")
    session.add(vendor)
    session.flush()
    plan = AiPlan(
        vendor_id=vendor.id,
        plan_name="Pro",
        slug="pro",
        billing_type="subscription",
        price_amount=20,
        price_currency="USD",
    )
    session.add(plan)
    session.flush()
    borrower = Member(
        team_id=team.id,
        display_name="借用人",
        channel_user_id="borrower-1",
        status="active",
    )
    session.add(borrower)
    session.flush()
    for index, account_id in enumerate(("acc-a", "acc-b")):
        session.add(
            AiAccount(
                id=account_id,
                vendor_id=vendor.id,
                plan_id=plan.id,
                team_id=team.id,
                account_identifier=f"{account_id}@x.com",
                status="shared",
                renews_on=date(2026, 9, 1),
                usage_resets_on=date(2026, 8, 1),
            )
        )
        session.add(
            AiAccountCredential(
                id=f"cred-{account_id}",
                account_id=account_id,
                vendor_id=vendor.id,
                credential_type="api_key",
                encrypted_value="x",
                key_hint="x",
                bound_by_member_id=borrower.id,
            )
        )
        session.add(_snapshot(account_id, 10.0 + index))
    session.commit()
    yield {"session": session, "team": team, "borrower": borrower}
    session.close()


@pytest.fixture(autouse=True)
def _clean_state():
    reset_auto_lender_state()
    yield
    reset_auto_lender_state()


def test_lender_mode_constants():
    assert LENDER_MODE_MANUAL == "manual"
    assert LENDER_MODE_AUTO == "auto"
    assert VALID_LENDER_MODES == frozenset({"manual", "auto"})


def test_resolve_auto_lender_prefers_roomier_account(env):
    session = env["session"]
    resolved = resolve_auto_lender(
        session, env["team"].id, borrower_member_id=env["borrower"].id, now=NOW
    )
    # acc-a 余量更宽 → 首选
    assert [row["account_id"] for row in resolved["ranked"]] == ["acc-a", "acc-b"]
    assert resolved["best"]["account_id"] == "acc-a"


def test_resolve_auto_lender_excludes_borrower_own_accounts(env):
    session = env["session"]
    resolved = resolve_auto_lender(
        session,
        env["team"].id,
        borrower_member_id=env["borrower"].id,
        exclude_account_ids={"acc-a"},
        now=NOW,
    )
    assert [row["account_id"] for row in resolved["ranked"]] == ["acc-b"]


def test_resolve_auto_lender_requires_both_buckets_when_model_unknown(env):
    """模型未知 → Quota Pool unknown：只有一桶有余量的账号不能借。"""
    session = env["session"]
    # captured_at 必须严格晚于 fixture 的快照：latest_snapshots_for_accounts
    # 按 MAX(captured_at) 取最新，同刻会 join 命中两行、结果不确定
    session.add(
        AccountQuotaSnapshot(
            account_id="acc-a",
            captured_at=NOW + timedelta(minutes=1),
            cycle_start=date(2026, 7, 1),
            cycle_end=date(2026, 8, 1),
            limit_cents=7000,
            used_cents=1400,
            remaining_cents=5600,
            total_pct=20.0,
            auto_pct=100.0,  # auto 桶已满
            api_pct=20.0,
        )
    )
    session.commit()

    resolved = resolve_auto_lender(
        session, env["team"].id, borrower_member_id=env["borrower"].id, now=NOW
    )
    assert [row["account_id"] for row in resolved["ranked"]] == ["acc-b"]
    assert {e["account_id"]: e["reason"] for e in resolved["excluded"]} == {
        "acc-a": "exhausted"
    }

    # 明确指定 api 桶时该账号可用
    resolved_api = resolve_auto_lender(
        session,
        env["team"].id,
        borrower_member_id=env["borrower"].id,
        model="claude-4-sonnet",
        now=NOW,
    )
    assert "acc-a" in [row["account_id"] for row in resolved_api["ranked"]]


def _picked_result(**decision_overrides) -> dict:
    decision = {
        "picked_by": "jev",
        "fallback_reason": None,
        "model": "typesafe/jev-1.13",
        "confidence": 0.9,
        "cached": False,
        "probabilities": {"acc-a": 0.8},
        "owner_safe": {"acc-a": True},
    }
    decision.update(decision_overrides)
    return {
        "ranked": [
            {
                "account_id": "acc-a",
                "account_identifier": "acc-a@x.com",
                "picked": True,
            }
        ],
        "excluded": [],
        "decision": decision,
    }


def test_audit_event_recorded_for_jev_pick(env):
    from pulse.storage.models import ProxyEvent

    session = env["session"]
    record_auto_lender_decision(session, _picked_result())
    session.commit()

    events = list(
        session.scalars(
            select(ProxyEvent).where(ProxyEvent.event_type == "lender_auto_pick")
        )
    )
    assert len(events) == 1
    detail = json.loads(events[0].detail)
    assert detail["picked_by"] == "jev"
    assert detail["account_id"] == "acc-a"
    assert detail["confidence"] == 0.9
    assert detail["fallback_reason"] is None


def test_audit_event_recorded_for_fallback_reason(env):
    from pulse.storage.models import ProxyEvent

    session = env["session"]
    record_auto_lender_decision(
        session,
        _picked_result(picked_by="algorithm", fallback_reason="low_confidence"),
    )
    session.commit()

    events = list(
        session.scalars(
            select(ProxyEvent).where(ProxyEvent.event_type == "lender_auto_pick")
        )
    )
    assert len(events) == 1
    assert json.loads(events[0].detail)["fallback_reason"] == "low_confidence"


def test_audit_event_skipped_when_auto_mode_off(env):
    from pulse.storage.models import ProxyEvent

    session = env["session"]
    record_auto_lender_decision(
        session,
        _picked_result(picked_by="algorithm", fallback_reason="auto_mode_off"),
    )
    session.commit()

    events = list(
        session.scalars(
            select(ProxyEvent).where(ProxyEvent.event_type == "lender_auto_pick")
        )
    )
    assert events == []


def test_audit_event_skipped_for_plain_algorithm_pick(env):
    """纯算法分且无回落原因不写事件，避免池轮询/预览刷爆事件表。"""
    from pulse.storage.models import ProxyEvent

    session = env["session"]
    record_auto_lender_decision(
        session, _picked_result(picked_by="algorithm", fallback_reason=None)
    )
    session.commit()

    events = list(
        session.scalars(
            select(ProxyEvent).where(ProxyEvent.event_type == "lender_auto_pick")
        )
    )
    assert events == []
