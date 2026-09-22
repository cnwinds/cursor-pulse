"""Auto Lender：硬过滤 → 算法分 → Jev 决策 → 护栏 → 最终序。

硬过滤与算法分始终权威：Jev 只对存活候选重排，任何护栏不通过都回落算法分。
Jev 跑在池刷新与借用发放上，不在请求链路里；同一候选特征在 TTL 内复用
决策，既防抖动也省调用。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable

from pulse.config import LoanSelectionConfig
from pulse.llm.jev import (
    JevClient,
    JevDecision,
    JevError,
    build_choice_question,
    build_noul_question,
)
from pulse.tool_center.burn_rate import LenderCandidate, explain_lender_selection
from pulse.tool_center.snapshot_headroom import QuotaPoolKind
from pulse.util.ttl_cache import TTLCache

logger = logging.getLogger(__name__)

PICK_QUESTION = "pick"
OWNER_QUESTION_PREFIX = "safe_for_owner_"
# noul 概率达到该值即视为「会影响主负责人」
OWNER_UNSAFE_PROBABILITY = 0.5

PICKED_BY_ALGORITHM = "algorithm"
PICKED_BY_JEV = "jev"


@dataclass
class AutoLenderDecision:
    """本轮选号结果：谁选中、回落原因、置信度与概率。"""

    picked_by: str = PICKED_BY_ALGORITHM
    fallback_reason: str | None = None
    model: str | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    owner_safe: dict[str, bool] = field(default_factory=dict)
    cached: bool = False
    usage: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """转成可直接进 API 响应 / 审计事件的普通字典。"""
        return {
            "picked_by": self.picked_by,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "owner_safe": self.owner_safe,
            "cached": self.cached,
            "usage": self.usage,
        }


# 决策缓存：feature_key -> (account_id, confidence, probabilities, owner_safe)
# 键里含 hours_to_deadline / surplus 这类连续变化字段，惰性淘汰几乎不触发，
# 因此必须有硬上限，否则长驻进程里只增不减。
CACHE_MAX_ENTRIES = 512
_decision_cache = TTLCache(CACHE_MAX_ENTRIES)
_breaker_lock = threading.Lock()
_breaker_state = {"failures": 0, "open_until": 0.0}


def reset_auto_lender_state() -> None:
    """清空决策缓存与熔断状态（测试 / 配置变更后）。"""
    _decision_cache.clear()
    with _breaker_lock:
        _breaker_state["failures"] = 0
        _breaker_state["open_until"] = 0.0


def _feature_key(
    rows: list[dict], pool: QuotaPoolKind | None, cfg: LoanSelectionConfig
) -> str:
    """候选特征指纹：特征没变就不重复问 Jev。

    刻意不含 minutes_since_switch —— 它每分钟都在变，会让缓存永不命中。
    """
    parts = [
        "|".join(
            f"{r['account_id']}:{r.get('pool_headroom_pct')}:"
            f"{r.get('pool_surplus_cents')}:{r.get('score_adjust')}:"
            f"{r.get('hours_to_deadline')}:{r.get('active_loans')}"
            for r in rows
        ),
        f"pool={pool}",
        f"top={cfg.auto_top_n}",
        f"conf={cfg.auto_min_confidence}",
        f"margin={cfg.auto_min_margin}",
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _cache_get(key: str, ttl_seconds: float):
    """读决策缓存（TTL 见 :mod:`pulse.util.ttl_cache`）。"""
    return _decision_cache.get(key, ttl_seconds)


def _cache_put(
    key: str,
    account_id: str,
    confidence: float | None,
    probabilities: dict[str, float],
    owner_safe: dict[str, bool],
    *,
    ttl_seconds: float = 0.0,
) -> None:
    """写决策缓存；ttl 只用于超上限时清理过期项。"""
    _decision_cache.put(
        key,
        (account_id, confidence, probabilities, owner_safe),
        ttl_seconds=ttl_seconds,
    )


def _breaker_open() -> bool:
    with _breaker_lock:
        return time.monotonic() < _breaker_state["open_until"]


def _record_failure(jev_config=None) -> None:
    threshold = getattr(jev_config, "failure_threshold", 3)
    cooldown = getattr(jev_config, "cooldown_seconds", 300.0)
    with _breaker_lock:
        _breaker_state["failures"] += 1
        if _breaker_state["failures"] >= threshold:
            _breaker_state["open_until"] = time.monotonic() + cooldown
            logger.warning(
                "auto lender: jev circuit opened for %.0fs after %d failures",
                cooldown,
                _breaker_state["failures"],
            )


def _record_success() -> None:
    with _breaker_lock:
        _breaker_state["failures"] = 0
        _breaker_state["open_until"] = 0.0


def _candidate_summary(row: dict) -> str:
    """给 Jev 的一句话候选描述（choice 的 criteria 文本）。"""
    owner = row.get("primary_member_name") or "unassigned"
    surplus_usd = round((row.get("pool_surplus_cents") or 0) / 100.0, 2)
    return (
        f"owner={owner}; pool_headroom={row.get('pool_headroom_pct')}%; "
        f"idle_surplus_usd={surplus_usd}; "
        f"hours_to_reset={row.get('hours_to_deadline')}; "
        f"active_loans={row.get('active_loans')}; "
        f"manual_score_delta={row.get('score_adjust')}; "
        f"minutes_since_last_switch={row.get('minutes_since_switch')}; "
        f"quota_state={row.get('status')}"
    )


def _build_state(
    rows: list[dict], *, pool: QuotaPoolKind | None, cfg: LoanSelectionConfig
) -> dict:
    """压缩后的候选特征，不是原始 DB 行。"""
    return {
        "task": "Choose the best Cursor account to lend to a borrower right now.",
        "quota_pool": pool or "unknown",
        "policy": {
            "goal": (
                "Use up quota that would otherwise be reset unused, while not "
                "harming the account's primary owner."
            ),
            "prefer": [
                "accounts whose pool headroom is projected to go unused before reset",
                "accounts closer to their reset deadline",
                "accounts with fewer active loans",
            ],
            "avoid": [
                "accounts where the primary owner is projected to need the pool",
                "accounts just switched to or from (switch dwell)",
                "accounts with little headroom on the requested quota pool",
            ],
        },
        "candidates": [
            {
                "account_id": r["account_id"],
                "primary_owner": r.get("primary_member_name"),
                "quota_pool": r.get("pool"),
                "pool_headroom_pct": r.get("pool_headroom_pct"),
                "pool_idle_surplus_usd": round(
                    (r.get("pool_surplus_cents") or 0) / 100.0, 2
                ),
                "total_pct": r.get("total_pct"),
                "auto_pct": r.get("auto_pct"),
                "api_pct": r.get("api_pct"),
                "hours_to_reset": r.get("hours_to_deadline"),
                "days_to_reset": r.get("days_to_deadline"),
                "active_loans": r.get("active_loans"),
                "manual_score_delta": r.get("score_adjust"),
                "algorithm_score": r.get("computed_score"),
                "minutes_since_last_switch": r.get("minutes_since_switch"),
                "quota_state": r.get("status"),
            }
            for r in rows
        ],
        "constraints": {
            "switch_dwell_minutes": cfg.min_switch_minutes,
            "max_active_loans_per_account": cfg.max_active_loans_per_account,
        },
    }


def _build_questions(rows: list[dict]) -> dict[str, dict]:
    questions: dict[str, dict] = {
        PICK_QUESTION: build_choice_question(
            "Which account should be lent to this borrower?",
            {r["account_id"]: _candidate_summary(r) for r in rows},
        )
    }
    for row in rows:
        questions[f"{OWNER_QUESTION_PREFIX}{row['account_id']}"] = build_noul_question(
            "Would lending this account right now risk the primary owner's "
            "remaining quota on the requested pool?",
            yes=(
                "The primary owner is on track to consume this pool up to or past "
                "its reset, so the loan would take quota they still need."
            ),
            no=(
                "The primary owner has enough headroom that this loan will not "
                "reduce what they can use before the reset."
            ),
        )
    return questions


def _owner_safety(decision: JevDecision, account_ids: list[str]) -> dict[str, bool]:
    """每个候选的主负责人安全性；Jev 没答的账号不出现在结果里。"""
    out: dict[str, bool] = {}
    for account_id in account_ids:
        answer = decision.answer(f"{OWNER_QUESTION_PREFIX}{account_id}")
        if answer is None:
            continue
        probability = answer.probability
        if probability is None:
            continue
        out[account_id] = probability < OWNER_UNSAFE_PROBABILITY
    return out


def _margin_ok(
    probabilities: dict[str, float], picked: str, min_margin: float
) -> bool:
    """首选概率与次优之间的差距是否够大；概率缺失时不拦。"""
    if len(probabilities) < 2 or picked not in probabilities:
        return True
    others = [p for name, p in probabilities.items() if name != picked]
    if not others:
        return True
    return (probabilities[picked] - max(others)) >= min_margin


def _evaluate_jev(
    decision: JevDecision, rows: list[dict], cfg: LoanSelectionConfig
) -> tuple[str | None, str | None, float | None, dict[str, float], dict[str, bool]]:
    """护栏判定。返回 (选中的 account_id 或 None, 回落原因, confidence, probabilities, owner_safe)。"""
    account_ids = [r["account_id"] for r in rows]
    answer = decision.answer(PICK_QUESTION)
    if answer is None:
        return None, "no_pick_answer", None, {}, {}
    confidence = answer.confidence
    probabilities = answer.probabilities
    owner_safe = _owner_safety(decision, account_ids)
    picked = answer.choice
    if picked is None:
        return None, "no_choice", confidence, probabilities, owner_safe
    if picked not in account_ids:
        return None, "unknown_account", confidence, probabilities, owner_safe
    if confidence is not None and confidence < cfg.auto_min_confidence:
        return None, "low_confidence", confidence, probabilities, owner_safe
    if not _margin_ok(probabilities, picked, cfg.auto_min_margin):
        return None, "narrow_margin", confidence, probabilities, owner_safe
    if owner_safe.get(picked) is False:
        return None, "owner_unsafe", confidence, probabilities, owner_safe
    return picked, None, confidence, probabilities, owner_safe


def _promote(ranked: list[dict], picked: str) -> list[dict]:
    """把 Jev 选中的账号提到首位，其余保持算法序。"""
    head = [row for row in ranked if row["account_id"] == picked]
    tail = [row for row in ranked if row["account_id"] != picked]
    return head + tail


def _result(
    ranked: list[dict],
    excluded: list[dict],
    decision: AutoLenderDecision,
    on_decision: Callable[[dict], None] | None,
) -> dict:
    out: list[dict] = []
    for index, row in enumerate(ranked):
        item = dict(row)
        item["picked"] = index == 0
        out.append(item)
    result = {"ranked": out, "excluded": excluded, "decision": decision.as_dict()}
    if on_decision is not None:
        try:
            on_decision(result)
        except Exception:
            logger.exception("auto lender: on_decision hook failed")
    return result


def rank_lenders(
    candidates: list[LenderCandidate],
    *,
    loan_selection: LoanSelectionConfig | None = None,
    pool: QuotaPoolKind | None = None,
    today: date | None = None,
    now: datetime | None = None,
    enforce_loan_cap: bool = True,
    exclude_at_loan_cap: bool | None = None,
    jev: JevClient | None = None,
    jev_config=None,
    on_decision: Callable[[dict], None] | None = None,
) -> dict:
    """完整选号：返回 ``{"ranked", "excluded", "decision"}``。

    ``ranked[0]`` 即最终选定账号（Jev 通过护栏时是 Jev 的选择，否则是算法首选）。
    """
    cfg = loan_selection or LoanSelectionConfig()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if today is None:
        today = now.date()

    board = explain_lender_selection(
        candidates,
        today,
        loan_selection=cfg,
        now=now,
        enforce_loan_cap=enforce_loan_cap,
        exclude_at_loan_cap=exclude_at_loan_cap,
        pool=pool,
    )
    ranked = board["ranked"]
    excluded = board["excluded"]

    if jev is None:
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(fallback_reason="jev_unavailable"),
            on_decision,
        )

    top = ranked[: cfg.auto_top_n]
    if len(top) < 2:
        # 只有一个候选时重排没有意义，也不值得付一次调用
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(fallback_reason="insufficient_candidates"),
            on_decision,
        )

    key = _feature_key(top, pool, cfg)
    cached = _cache_get(key, cfg.auto_cache_seconds)
    if cached is not None:
        picked, confidence, probabilities, owner_safe = cached
        return _result(
            _promote(ranked, picked),
            excluded,
            AutoLenderDecision(
                picked_by=PICKED_BY_JEV,
                confidence=confidence,
                probabilities=probabilities,
                owner_safe=owner_safe,
                cached=True,
            ),
            on_decision,
        )

    if _breaker_open():
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(fallback_reason="circuit_open"),
            on_decision,
        )

    try:
        jev_decision = jev.decide(
            state=_build_state(top, pool=pool, cfg=cfg),
            questions=_build_questions(top),
        )
    except JevError as exc:
        _record_failure(jev_config)
        logger.warning("auto lender: jev call failed: %s", exc)
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(fallback_reason="jev_error"),
            on_decision,
        )

    _record_success()
    picked, reason, confidence, probabilities, owner_safe = _evaluate_jev(
        jev_decision, top, cfg
    )
    decision = AutoLenderDecision(
        picked_by=PICKED_BY_JEV if picked else PICKED_BY_ALGORITHM,
        fallback_reason=reason,
        model=jev_decision.model,
        confidence=confidence,
        probabilities=probabilities,
        owner_safe=owner_safe,
        usage=jev_decision.usage,
    )
    if picked is None:
        return _result(ranked, excluded, decision, on_decision)

    _cache_put(
        key,
        picked,
        confidence,
        probabilities,
        owner_safe,
        ttl_seconds=cfg.auto_cache_seconds,
    )
    return _result(_promote(ranked, picked), excluded, decision, on_decision)
