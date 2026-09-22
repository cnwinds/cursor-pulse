from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from pulse.config import LoanSelectionConfig
from pulse.llm.jev import JevAnswer, JevDecision, JevError
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.auto_lender import (
    OWNER_QUESTION_PREFIX,
    PICK_QUESTION,
    rank_lenders,
    reset_auto_lender_state,
)
from pulse.tool_center.burn_rate import LenderCandidate

TODAY = date(2026, 7, 10)
NOW = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)


def _snapshot(*, account_id: str, total_pct: float, api_pct: float) -> AccountQuotaSnapshot:
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
        api_pct=api_pct,
    )


def _candidate(account_id: str, *, total_pct: float, api_pct: float, bound_at=None):
    return LenderCandidate(
        snapshot=_snapshot(account_id=account_id, total_pct=total_pct, api_pct=api_pct),
        account_id=account_id,
        account_identifier=f"{account_id}@x.com",
        primary_member_name=f"owner-{account_id}",
        bound_at=bound_at,
    )


def _two_candidates():
    # 两个都健康；算法侧 a 略优（total_pct 更低）
    return [
        _candidate("acc-a", total_pct=10.0, api_pct=10.0),
        _candidate("acc-b", total_pct=20.0, api_pct=20.0),
    ]


class FakeJev:
    def __init__(self, decision=None, error=None):
        self.decision = decision
        self.error = error
        self.calls = 0
        self.last_state = None
        self.last_questions = None

    def decide(self, *, state, questions, session_id=None):
        self.calls += 1
        self.last_state = state
        self.last_questions = questions
        if self.error is not None:
            raise self.error
        return self.decision


def _decision(choice, *, confidence=0.9, probabilities=None, owner=None):
    answers = {
        PICK_QUESTION: JevAnswer(
            name=PICK_QUESTION,
            raw={
                "choice": choice,
                "confidence": confidence,
                "probabilities": probabilities
                if probabilities is not None
                else {"acc-a": 0.3, "acc-b": 0.7},
            },
        )
    }
    for account_id, probability in (owner or {}).items():
        answers[f"{OWNER_QUESTION_PREFIX}{account_id}"] = JevAnswer(
            name=f"{OWNER_QUESTION_PREFIX}{account_id}", raw=probability
        )
    return JevDecision(answers=answers, model="typesafe/jev-1.13", usage={})


@pytest.fixture(autouse=True)
def _clean_state():
    reset_auto_lender_state()
    yield
    reset_auto_lender_state()


def _auto_cfg(**kwargs) -> LoanSelectionConfig:
    base = {"auto_cache_seconds": 600.0}
    base.update(kwargs)
    return LoanSelectionConfig(**base)


def test_legacy_auto_mode_flag_does_not_gate_jev():
    """选号不再看 loan_selection.auto_mode，只看有没有 Jev 客户端。"""
    jev = FakeJev(decision=_decision("acc-b"))
    board = rank_lenders(
        _two_candidates(),
        loan_selection=LoanSelectionConfig(auto_mode=False, auto_cache_seconds=600.0),
        today=TODAY,
        now=NOW,
        jev=jev,
    )
    assert [r["account_id"] for r in board["ranked"]] == ["acc-b", "acc-a"]
    assert board["decision"]["picked_by"] == "jev"
    assert jev.calls == 1


def test_jev_unavailable_falls_back():
    board = rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=None
    )
    assert board["decision"]["picked_by"] == "algorithm"
    assert board["decision"]["fallback_reason"] == "jev_unavailable"


def test_jev_pick_is_promoted_to_front():
    jev = FakeJev(decision=_decision("acc-b"))
    board = rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=jev
    )
    assert [r["account_id"] for r in board["ranked"]] == ["acc-b", "acc-a"]
    assert board["ranked"][0]["picked"] is True
    assert board["ranked"][1]["picked"] is False
    assert board["decision"]["picked_by"] == "jev"
    assert board["decision"]["fallback_reason"] is None
    assert board["decision"]["confidence"] == 0.9
    assert jev.calls == 1


def test_state_and_questions_carry_candidate_features():
    jev = FakeJev(decision=_decision("acc-a"))
    rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=jev
    )
    state = jev.last_state
    assert state["quota_pool"] == "unknown"
    assert {c["account_id"] for c in state["candidates"]} == {"acc-a", "acc-b"}
    assert state["constraints"]["switch_dwell_minutes"] == 30.0
    # 每个候选一问「是否影响主负责人」，且 choice 的 criteria 覆盖全部候选
    assert set(jev.last_questions) == {
        PICK_QUESTION,
        f"{OWNER_QUESTION_PREFIX}acc-a",
        f"{OWNER_QUESTION_PREFIX}acc-b",
    }
    assert set(jev.last_questions[PICK_QUESTION]["criteria"]) == {"acc-a", "acc-b"}


def test_low_confidence_falls_back():
    jev = FakeJev(decision=_decision("acc-b", confidence=0.2))
    board = rank_lenders(
        _two_candidates(),
        loan_selection=_auto_cfg(auto_min_confidence=0.5),
        today=TODAY,
        now=NOW,
        jev=jev,
    )
    assert [r["account_id"] for r in board["ranked"]] == ["acc-a", "acc-b"]
    assert board["decision"]["picked_by"] == "algorithm"
    assert board["decision"]["fallback_reason"] == "low_confidence"
    assert board["decision"]["confidence"] == 0.2


def test_narrow_margin_falls_back():
    jev = FakeJev(
        decision=_decision(
            "acc-b", probabilities={"acc-a": 0.48, "acc-b": 0.52}, confidence=0.9
        )
    )
    board = rank_lenders(
        _two_candidates(),
        loan_selection=_auto_cfg(auto_min_margin=0.05),
        today=TODAY,
        now=NOW,
        jev=jev,
    )
    assert board["decision"]["fallback_reason"] == "narrow_margin"


def test_unknown_account_falls_back():
    jev = FakeJev(decision=_decision("acc-zzz"))
    board = rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=jev
    )
    assert board["decision"]["fallback_reason"] == "unknown_account"


def test_missing_pick_answer_falls_back():
    jev = FakeJev(decision=JevDecision(answers={}, model="m"))
    board = rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=jev
    )
    assert board["decision"]["fallback_reason"] == "no_pick_answer"


def test_owner_unsafe_blocks_the_pick():
    """Jev 选中 b，但 b 被判定会侵占主负责人预留 → 回落算法首选。"""
    jev = FakeJev(decision=_decision("acc-b", owner={"acc-b": 0.9, "acc-a": 0.05}))
    board = rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=jev
    )
    assert [r["account_id"] for r in board["ranked"]] == ["acc-a", "acc-b"]
    assert board["decision"]["fallback_reason"] == "owner_unsafe"
    assert board["decision"]["owner_safe"] == {"acc-b": False, "acc-a": True}


def test_jev_error_falls_back():
    jev = FakeJev(error=JevError("boom"))
    board = rank_lenders(
        _two_candidates(), loan_selection=_auto_cfg(), today=TODAY, now=NOW, jev=jev
    )
    assert board["decision"]["fallback_reason"] == "jev_error"
    assert [r["account_id"] for r in board["ranked"]] == ["acc-a", "acc-b"]


def test_decision_cache_avoids_repeat_calls():
    jev = FakeJev(decision=_decision("acc-b"))
    cfg = _auto_cfg()
    first = rank_lenders(_two_candidates(), loan_selection=cfg, today=TODAY, now=NOW, jev=jev)
    second = rank_lenders(_two_candidates(), loan_selection=cfg, today=TODAY, now=NOW, jev=jev)
    assert jev.calls == 1
    assert first["decision"]["cached"] is False
    assert second["decision"]["cached"] is True
    assert [r["account_id"] for r in second["ranked"]] == ["acc-b", "acc-a"]


def test_cache_ignores_minutes_since_switch():
    """驻留分钟数一直在变，但不能让缓存失效。"""
    jev = FakeJev(decision=_decision("acc-b"))
    cfg = _auto_cfg()
    fresh = [
        _candidate("acc-a", total_pct=10.0, api_pct=10.0, bound_at=NOW),
        _candidate("acc-b", total_pct=20.0, api_pct=20.0, bound_at=NOW),
    ]
    older = [
        _candidate("acc-a", total_pct=10.0, api_pct=10.0, bound_at=NOW.replace(hour=11)),
        _candidate("acc-b", total_pct=20.0, api_pct=20.0, bound_at=NOW.replace(hour=11)),
    ]
    rank_lenders(fresh, loan_selection=cfg, today=TODAY, now=NOW, jev=jev)
    rank_lenders(older, loan_selection=cfg, today=TODAY, now=NOW, jev=jev)
    assert jev.calls == 1


def test_single_candidate_skips_jev():
    jev = FakeJev(decision=_decision("acc-a"))
    board = rank_lenders(
        [_candidate("acc-a", total_pct=10.0, api_pct=10.0)],
        loan_selection=_auto_cfg(),
        today=TODAY,
        now=NOW,
        jev=jev,
    )
    assert jev.calls == 0
    assert board["decision"]["fallback_reason"] == "insufficient_candidates"
    assert [r["account_id"] for r in board["ranked"]] == ["acc-a"]


def test_circuit_breaker_opens_after_threshold():
    from pulse.config import JevConfig

    cfg = _auto_cfg()
    jev = FakeJev(error=JevError("boom"))
    jev_cfg = JevConfig(enabled=True, api_key="k", failure_threshold=2, cooldown_seconds=300)
    for _ in range(2):
        rank_lenders(
            _two_candidates(),
            loan_selection=cfg,
            today=TODAY,
            now=NOW,
            jev=jev,
            jev_config=jev_cfg,
        )
    assert jev.calls == 2
    board = rank_lenders(
        _two_candidates(),
        loan_selection=cfg,
        today=TODAY,
        now=NOW,
        jev=jev,
        jev_config=jev_cfg,
    )
    assert board["decision"]["fallback_reason"] == "circuit_open"
    assert jev.calls == 2  # 熔断期间不再调用


def test_excluded_candidates_are_not_sent_to_jev():
    """耗尽账号在硬过滤阶段就被排除，不进 Jev 候选集。"""
    exhausted = _candidate("acc-full", total_pct=100.0, api_pct=100.0)
    jev = FakeJev(decision=_decision("acc-a"))
    board = rank_lenders(
        [*_two_candidates(), exhausted],
        loan_selection=_auto_cfg(),
        today=TODAY,
        now=NOW,
        jev=jev,
    )
    assert set(jev.last_questions[PICK_QUESTION]["criteria"]) == {"acc-a", "acc-b"}
    assert {e["account_id"]: e["reason"] for e in board["excluded"]} == {
        "acc-full": "exhausted"
    }


def test_pool_argument_is_forwarded_to_state_and_scoring():
    jev = FakeJev(decision=_decision("acc-a"))
    rank_lenders(
        _two_candidates(),
        loan_selection=_auto_cfg(),
        pool="api",
        today=TODAY,
        now=NOW,
        jev=jev,
    )
    assert jev.last_state["quota_pool"] == "api"
    assert jev.last_state["candidates"][0]["quota_pool"] == "api"


def test_decision_cache_stays_bounded():
    """键含随时间变化的字段，缓存必须有硬上限，否则长驻进程只增不减。"""
    import pulse.tool_center.auto_lender as mod

    cfg = _auto_cfg()
    # 每次用不同的候选特征 → 每次都产生新的 feature_key
    for index in range(mod.CACHE_MAX_ENTRIES + 40):
        jev = FakeJev(decision=_decision("acc-a"))
        rank_lenders(
            [
                _candidate("acc-a", total_pct=1.0 + index * 0.1, api_pct=1.0),
                _candidate("acc-b", total_pct=2.0 + index * 0.1, api_pct=2.0),
            ],
            loan_selection=cfg,
            today=TODAY,
            now=NOW,
            jev=jev,
        )
    assert len(mod._decision_cache) <= mod.CACHE_MAX_ENTRIES


def test_on_decision_hook_receives_result():
    jev = FakeJev(decision=_decision("acc-b"))
    seen = []
    rank_lenders(
        _two_candidates(),
        loan_selection=_auto_cfg(),
        today=TODAY,
        now=NOW,
        jev=jev,
        on_decision=seen.append,
    )
    assert len(seen) == 1
    assert seen[0]["decision"]["picked_by"] == "jev"
    assert seen[0]["ranked"][0]["account_id"] == "acc-b"


def test_on_decision_hook_failure_does_not_break_selection():
    jev = FakeJev(decision=_decision("acc-b"))

    def boom(_):
        raise RuntimeError("hook down")

    board = rank_lenders(
        _two_candidates(),
        loan_selection=_auto_cfg(),
        today=TODAY,
        now=NOW,
        jev=jev,
        on_decision=boom,
    )
    assert [r["account_id"] for r in board["ranked"]] == ["acc-b", "acc-a"]
