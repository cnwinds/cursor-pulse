from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from pulse.config import LoanSelectionConfig
from pulse.llm.jev import JevAnswer, JevDecision
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.auto_lender import (
    OWNER_QUESTION_PREFIX,
    PICK_QUESTION,
    reset_auto_lender_state,
)
from pulse.tool_center.key_loan_auto import (
    record_auto_lender_decision,
    reevaluate_auto_loans,
)
from pulse.tool_center.key_loan_delivery import (
    LENDER_MODE_AUTO,
    LENDER_MODE_MANUAL,
    VALID_LENDER_MODES,
)
from pulse.tool_center.key_loan_store import KeyLoanService
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


def _make_loan(session, env, *, source_account_id, bound_at):
    """直接落一条 active 的 auto 借用记录（绕过远端 Cursor 调用）。"""
    svc = KeyLoanService(session, "enc-key")
    loan = svc.create_loan_record(
        source_account_id=source_account_id,
        credential_id=f"cred-{source_account_id}",
        borrower_member_id=env["borrower"].id,
        baseline_used_cents=0,
        lender_mode=LENDER_MODE_AUTO,
        source_bound_at=bound_at,
    )
    session.flush()
    return loan


class FakeJev:
    def __init__(self, decision=None, error=None):
        self.decision = decision
        self.error = error
        self.calls = 0

    def decide(self, *, state, questions, session_id=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.decision


def _pick(account_id, *, confidence=0.9):
    return JevDecision(
        answers={
            PICK_QUESTION: JevAnswer(
                name=PICK_QUESTION,
                raw={
                    "choice": account_id,
                    "confidence": confidence,
                    "probabilities": {"acc-a": 0.2, "acc-b": 0.8},
                },
            ),
            f"{OWNER_QUESTION_PREFIX}acc-a": JevAnswer(
                name=f"{OWNER_QUESTION_PREFIX}acc-a", raw=0.05
            ),
            f"{OWNER_QUESTION_PREFIX}acc-b": JevAnswer(
                name=f"{OWNER_QUESTION_PREFIX}acc-b", raw=0.05
            ),
        },
        model="typesafe/jev-1.13",
    )


def _auto_cfg(**kwargs) -> LoanSelectionConfig:
    base = {"auto_mode": True, "min_switch_minutes": 30.0, "auto_cache_seconds": 600.0}
    base.update(kwargs)
    return LoanSelectionConfig(**base)


def _no_reassign(*args, **kwargs):
    raise AssertionError("reassign must not be called")


def test_lender_mode_constants():
    assert LENDER_MODE_MANUAL == "manual"
    assert LENDER_MODE_AUTO == "auto"
    assert VALID_LENDER_MODES == frozenset({"manual", "auto"})


def test_reevaluate_skips_when_auto_mode_off(env):
    session = env["session"]
    _make_loan(session, env, source_account_id="acc-a", bound_at=NOW - timedelta(hours=5))
    session.commit()

    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=LoanSelectionConfig(auto_mode=False),
        now=NOW,
    )
    assert stats == {
        "checked": 0,
        "switched": 0,
        "skipped_dwell": 0,
        "skipped_traffic": 0,
        "skipped_no_gain": 0,
    }


def test_reevaluate_respects_switch_dwell(env):
    session = env["session"]
    _make_loan(
        session, env, source_account_id="acc-a", bound_at=NOW - timedelta(minutes=10)
    )
    session.commit()

    stats = reevaluate_auto_loans(
        session, "enc-key", team_id=env["team"].id, loan_selection=_auto_cfg(), now=NOW
    )
    assert stats["checked"] == 1
    assert stats["skipped_dwell"] == 1
    assert stats["switched"] == 0


def test_reevaluate_switches_after_dwell_when_gain_exceeds_margin(env, monkeypatch):
    """满驻留窗口 + 分差足够 → 换绑到更优账号。"""
    session = env["session"]
    loan = _make_loan(
        session, env, source_account_id="acc-b", bound_at=NOW - timedelta(minutes=45)
    )
    session.commit()

    captured: dict = {}

    def fake_reassign(session_, encryption_key, **kwargs):
        captured.update(kwargs)
        loan.source_account_id = kwargs["new_source_account_id"]
        loan.source_bound_at = NOW
        return {
            "loan_id": loan.id,
            "old_source_account_identifier": "acc-b@x.com",
            "source_account_identifier": "acc-a@x.com",
            "old_remote_revoked": False,
        }

    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.reassign_loan_source", fake_reassign
    )
    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.finalize_reassign_old_remote_revoke",
        lambda *a, **k: False,
    )

    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=_auto_cfg(auto_switch_margin=0.0),
        now=NOW,
    )
    assert stats["switched"] == 1
    assert captured["new_source_account_id"] == "acc-a"
    assert loan.source_account_id == "acc-a"


def test_reevaluate_skips_when_gain_below_margin(env, monkeypatch):
    session = env["session"]
    _make_loan(
        session, env, source_account_id="acc-b", bound_at=NOW - timedelta(minutes=45)
    )
    session.commit()

    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.reassign_loan_source", _no_reassign
    )

    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        # 分差不可能超过 99，等价于永不换绑
        loan_selection=_auto_cfg(auto_switch_margin=99.0),
        now=NOW,
    )
    assert stats["switched"] == 0
    assert stats["skipped_no_gain"] == 1


def test_reevaluate_skips_loan_with_recent_traffic(env, monkeypatch):
    session = env["session"]
    loan = _make_loan(
        session, env, source_account_id="acc-b", bound_at=NOW - timedelta(hours=2)
    )
    from pulse.storage.models import ProxyKeyUsage

    session.add(
        ProxyKeyUsage(
            id="u1",
            loan_id=loan.id,
            model="composer-2.5",
            total_tokens=10,
            cost_cents=1,
            ts=NOW - timedelta(minutes=2),
        )
    )
    session.commit()

    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.reassign_loan_source", _no_reassign
    )

    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=_auto_cfg(auto_switch_margin=0.0),
        active_traffic_minutes=10.0,
        now=NOW,
    )
    assert stats["skipped_traffic"] == 1
    assert stats["switched"] == 0


def test_reevaluate_ignores_manual_loans(env):
    session = env["session"]
    svc = KeyLoanService(session, "enc-key")
    svc.create_loan_record(
        source_account_id="acc-b",
        credential_id="cred-acc-b",
        borrower_member_id=env["borrower"].id,
        baseline_used_cents=0,
        lender_mode=LENDER_MODE_MANUAL,
        source_bound_at=NOW - timedelta(hours=5),
    )
    session.commit()

    stats = reevaluate_auto_loans(
        session, "enc-key", team_id=env["team"].id, loan_selection=_auto_cfg(), now=NOW
    )
    assert stats["checked"] == 0


def test_reevaluate_no_gain_when_best_is_current(env):
    """当前账号就是最优 → 不换绑、也不计 skipped_no_gain。"""
    session = env["session"]
    _make_loan(session, env, source_account_id="acc-a", bound_at=NOW - timedelta(hours=3))
    session.commit()

    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=_auto_cfg(auto_switch_margin=0.0),
        now=NOW,
    )
    assert stats["switched"] == 0
    assert stats["skipped_no_gain"] == 0


def test_resolve_auto_lender_excludes_borrower_own_accounts(env):
    """借用不借自己的号：显式传入的排除集与名下账号都生效。"""
    from pulse.tool_center.key_loan_auto import resolve_auto_lender

    session = env["session"]
    resolved = resolve_auto_lender(
        session,
        env["team"].id,
        borrower_member_id=env["borrower"].id,
        exclude_account_ids={"acc-a"},
        now=NOW,
    )
    assert [row["account_id"] for row in resolved["ranked"]] == ["acc-b"]


def test_reevaluate_groups_by_borrower_without_losing_switches(env, monkeypatch):
    """同一借用人多笔 auto 借用：候选只构建一次，但每笔都独立判断换绑。"""
    session = env["session"]
    first = _make_loan(
        session, env, source_account_id="acc-b", bound_at=NOW - timedelta(hours=2)
    )
    second = _make_loan(
        session, env, source_account_id="acc-b", bound_at=NOW - timedelta(hours=3)
    )
    session.commit()

    build_calls = {"n": 0}
    from pulse.tool_center import key_loan_auto as mod

    real_build = mod.build_lender_candidates

    def counting_build(*args, **kwargs):
        build_calls["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(mod, "build_lender_candidates", counting_build)

    switched: list[str] = []

    def fake_reassign(session_, encryption_key, **kwargs):
        switched.append(kwargs["loan_id"])
        return {
            "loan_id": kwargs["loan_id"],
            "old_source_account_identifier": "acc-b@x.com",
            "source_account_identifier": "acc-a@x.com",
            "old_remote_revoked": False,
        }

    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.reassign_loan_source", fake_reassign
    )
    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.finalize_reassign_old_remote_revoke",
        lambda *a, **k: False,
    )

    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=_auto_cfg(auto_switch_margin=0.0),
        now=NOW,
    )
    assert stats["switched"] == 2
    assert sorted(switched) == sorted([first.id, second.id])
    # 两笔同借用人 → 候选只构建一次（分组复用）
    assert build_calls["n"] == 1


def test_audit_event_recorded_for_jev_pick(env, monkeypatch):
    """Auto Lender 决策必须落审计事件（lender_auto_pick）。"""
    from pulse.storage.models import ProxyEvent

    session = env["session"]
    loan = _make_loan(
        session, env, source_account_id="acc-a", bound_at=NOW - timedelta(hours=2)
    )
    session.commit()

    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.reassign_loan_source",
        lambda session_, encryption_key, **kwargs: {
            "loan_id": loan.id,
            "old_source_account_identifier": "acc-a@x.com",
            "source_account_identifier": "acc-b@x.com",
            "old_remote_revoked": False,
        },
    )
    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.finalize_reassign_old_remote_revoke",
        lambda *a, **k: False,
    )

    jev = FakeJev(decision=_pick("acc-b"))
    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=_auto_cfg(auto_switch_margin=0.0),
        jev=jev,
        on_decision=lambda result: record_auto_lender_decision(session, result),
        now=NOW,
    )
    assert stats["switched"] == 1

    events = list(
        session.scalars(
            select(ProxyEvent).where(ProxyEvent.event_type == "lender_auto_pick")
        )
    )
    assert len(events) == 1
    detail = json.loads(events[0].detail)
    assert detail["picked_by"] == "jev"
    assert detail["account_id"] == "acc-b"
    assert detail["fallback_reason"] is None


def test_audit_event_skipped_when_auto_mode_off(env):
    from pulse.storage.models import ProxyEvent

    session = env["session"]
    _make_loan(session, env, source_account_id="acc-a", bound_at=NOW - timedelta(hours=3))
    session.commit()

    reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=LoanSelectionConfig(auto_mode=False),
        on_decision=lambda result: record_auto_lender_decision(session, result),
        now=NOW,
    )
    session.commit()
    events = list(
        session.scalars(
            select(ProxyEvent).where(ProxyEvent.event_type == "lender_auto_pick")
        )
    )
    assert events == []


def test_resolve_auto_lender_requires_both_buckets_when_model_unknown(env):
    """模型未知 → Quota Pool unknown：只有一桶有余量的账号不能借。"""
    from pulse.storage.models import AccountQuotaSnapshot

    session = env["session"]
    # captured_at 必须严格晚于 fixture 的快照：latest_snapshots_for_accounts
    # 按 MAX(captured_at) 取最新，同刻会命中两行、结果不确定
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

    from pulse.tool_center.key_loan_auto import resolve_auto_lender

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


def test_reevaluate_uses_jev_pick_for_auto_loans(env, monkeypatch):
    """Auto 模式 + Jev 可用：换绑目标就是 Jev 选中的账号。"""
    session = env["session"]
    loan = _make_loan(
        session, env, source_account_id="acc-a", bound_at=NOW - timedelta(hours=2)
    )
    session.commit()

    captured: dict = {}

    def fake_reassign(session_, encryption_key, **kwargs):
        captured.update(kwargs)
        return {
            "loan_id": loan.id,
            "old_source_account_identifier": "acc-a@x.com",
            "source_account_identifier": "acc-b@x.com",
            "old_remote_revoked": False,
        }

    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.reassign_loan_source", fake_reassign
    )
    monkeypatch.setattr(
        "pulse.tool_center.key_loan_issue.finalize_reassign_old_remote_revoke",
        lambda *a, **k: False,
    )

    jev = FakeJev(decision=_pick("acc-b"))
    stats = reevaluate_auto_loans(
        session,
        "enc-key",
        team_id=env["team"].id,
        loan_selection=_auto_cfg(auto_switch_margin=0.0),
        jev=jev,
        now=NOW,
    )
    assert jev.calls == 1
    assert stats["switched"] == 1
    assert captured["new_source_account_id"] == "acc-b"
