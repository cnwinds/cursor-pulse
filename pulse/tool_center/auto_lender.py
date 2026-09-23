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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

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
class _CachedJevPayload:
    """TTL 缓存里附带的 Jev 响应快照，供 UI 在 cache hit 时展示出参。"""

    account_id: str
    confidence: float | None
    probabilities: dict[str, float]
    owner_safe: dict[str, bool]
    model: str | None
    output: dict
    usage: dict


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
    jev_trace: dict | None = None

    def as_dict(self) -> dict:
        """转成可直接进 API 响应 / 审计事件的普通字典。"""
        out = {
            "picked_by": self.picked_by,
            "fallback_reason": self.fallback_reason,
            "model": self.model,
            "confidence": self.confidence,
            "probabilities": self.probabilities,
            "owner_safe": self.owner_safe,
            "cached": self.cached,
            "usage": self.usage,
        }
        if self.jev_trace is not None:
            out["jev_trace"] = self.jev_trace
        return out


# 决策缓存：feature_key -> _CachedJevPayload
# 键里含 hours_to_deadline / surplus 这类连续变化字段，惰性淘汰几乎不触发，
# 因此必须有硬上限，否则长驻进程里只增不减。
CACHE_MAX_ENTRIES = 512
_decision_cache = TTLCache(CACHE_MAX_ENTRIES)
_breaker_lock = threading.Lock()
_breaker_state = {"failures": 0, "open_until": 0.0}
_force_refresh_lock = threading.Lock()
_force_refresh_last: dict[str, float] = {}
FORCE_JEV_REFRESH_COOLDOWN_SECONDS = 30.0


def reset_auto_lender_state() -> None:
    """清空决策缓存与熔断状态（测试 / 配置变更后）。"""
    _decision_cache.clear()
    with _breaker_lock:
        _breaker_state["failures"] = 0
        _breaker_state["open_until"] = 0.0
    with _force_refresh_lock:
        _force_refresh_last.clear()


def try_force_jev_refresh(team_id: str, *, cooldown_seconds: float = FORCE_JEV_REFRESH_COOLDOWN_SECONDS) -> float | None:
    """管理员强制再打 Jev 时的进程内限流。成功返回 None，否则返回建议等待秒数。"""
    if cooldown_seconds <= 0:
        return None
    now = time.monotonic()
    with _force_refresh_lock:
        last = _force_refresh_last.get(team_id, 0.0)
        wait = cooldown_seconds - (now - last)
        if wait > 0:
            return wait
        _force_refresh_last[team_id] = now
    return None


def _feature_key(rows: list[dict], pool: QuotaPoolKind | None, cfg: LoanSelectionConfig) -> str:
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
    payload: _CachedJevPayload,
    *,
    ttl_seconds: float = 0.0,
) -> None:
    """写决策缓存；ttl 只用于超上限时清理过期项。"""
    _decision_cache.put(key, payload, ttl_seconds=ttl_seconds)


def _serialize_jev_output(decision: JevDecision) -> dict:
    answers = {
        name: answer.raw for name, answer in decision.answers.items()
    }
    out: dict = {"answers": answers}
    if decision.model:
        out["model"] = decision.model
    if decision.provider:
        out["provider"] = decision.provider
    if decision.usage:
        out["usage"] = decision.usage
    raw_id = decision.raw.get("id") if isinstance(decision.raw, dict) else None
    if raw_id:
        out["id"] = raw_id
    return out


def build_jev_input(
    rows: list[dict],
    *,
    pool: QuotaPoolKind | None,
    cfg: LoanSelectionConfig,
    model: str,
) -> dict:
    """构造 Decisions 请求的 input 视图（不含密钥）。"""
    return {
        "model": model,
        "state": _build_state(rows, pool=pool, cfg=cfg),
        "questions": _build_questions(rows),
        "top_n_account_ids": [r["account_id"] for r in rows],
    }


def _build_jev_guards(
    *,
    cfg: LoanSelectionConfig,
    pick_choice: str | None = None,
    confidence: float | None = None,
    probabilities: dict[str, float] | None = None,
    owner_safe: dict[str, bool] | None = None,
    fallback_reason: str | None = None,
) -> dict:
    return {
        "pick_choice": pick_choice,
        "confidence": confidence,
        "probabilities": probabilities or {},
        "owner_safe": owner_safe or {},
        "fallback_reason": fallback_reason,
        "thresholds": {
            "auto_min_confidence": cfg.auto_min_confidence,
            "auto_min_margin": cfg.auto_min_margin,
            "owner_unsafe_probability": OWNER_UNSAFE_PROBABILITY,
        },
    }


def _build_jev_trace(
    *,
    status: str,
    skip_reason: str | None = None,
    error_message: str | None = None,
    cached: bool = False,
    model: str | None = None,
    jev_input: dict | None = None,
    jev_output: dict | None = None,
    guards: dict | None = None,
) -> dict:
    meta: dict = {"status": status}
    if skip_reason:
        meta["skip_reason"] = skip_reason
    if error_message:
        meta["error_message"] = error_message
    if cached:
        meta["cached"] = True
    if model:
        meta["model"] = model
    trace: dict = {"meta": meta}
    if jev_input is not None:
        trace["input"] = jev_input
    if jev_output is not None:
        trace["output"] = jev_output
    if guards is not None:
        trace["guards"] = guards
    return trace


def mark_jev_trace_force_refresh(trace: dict | None) -> dict | None:
    if trace is None:
        return trace
    meta = dict(trace.get("meta") or {})
    meta["force_refresh"] = True
    out = dict(trace)
    out["meta"] = meta
    return out


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


def _build_state(rows: list[dict], *, pool: QuotaPoolKind | None, cfg: LoanSelectionConfig) -> dict:
    """压缩后的候选特征，不是原始 DB 行。"""
    return {
        "task": "Choose the best Cursor account to lend to a borrower right now.",
        "quota_pool": pool or "unknown",
        "policy": {
            "goal": (
                "Use up quota that would otherwise be reset unused, while not harming the account's primary owner."
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
                "pool_idle_surplus_usd": round((r.get("pool_surplus_cents") or 0) / 100.0, 2),
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
            "Would lending this account right now risk the primary owner's remaining quota on the requested pool?",
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


def _margin_ok(probabilities: dict[str, float], picked: str, min_margin: float) -> bool:
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
    *,
    force_refresh: bool = False,
) -> dict:
    out: list[dict] = []
    for index, row in enumerate(ranked):
        item = dict(row)
        item["picked"] = index == 0
        out.append(item)
    decision_dict = decision.as_dict()
    if force_refresh:
        decision_dict["jev_trace"] = mark_jev_trace_force_refresh(decision_dict.get("jev_trace"))
    result = {"ranked": out, "excluded": excluded, "decision": decision_dict}
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
    jev_bypass_cache: bool = False,
) -> dict:
    """完整选号：返回 ``{"ranked", "excluded", "decision"}``。

    ``ranked[0]`` 即最终选定账号（Jev 通过护栏时是 Jev 的选择，否则是算法首选）。
    """
    cfg = loan_selection or LoanSelectionConfig()
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
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
            AutoLenderDecision(
                fallback_reason="jev_unavailable",
                jev_trace=_build_jev_trace(
                    status="skipped",
                    skip_reason="jev_unavailable",
                ),
            ),
            on_decision,
            force_refresh=jev_bypass_cache,
        )

    top = ranked[: cfg.auto_top_n]
    jev_input = build_jev_input(top, pool=pool, cfg=cfg, model=jev.model)
    if len(top) < 2:
        # 只有一个候选时重排没有意义，也不值得付一次调用
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(
                fallback_reason="insufficient_candidates",
                jev_trace=_build_jev_trace(
                    status="skipped",
                    skip_reason="insufficient_candidates",
                    model=jev.model,
                    jev_input=jev_input,
                    guards=_build_jev_guards(cfg=cfg),
                ),
            ),
            on_decision,
            force_refresh=jev_bypass_cache,
        )

    key = _feature_key(top, pool, cfg)
    cached = None if jev_bypass_cache else _cache_get(key, cfg.auto_cache_seconds)
    if cached is not None:
        picked = cached.account_id
        confidence = cached.confidence
        probabilities = cached.probabilities
        owner_safe = cached.owner_safe
        pick_answer = cached.output.get("answers", {}).get(PICK_QUESTION)
        pick_choice = (
            pick_answer.get("choice") if isinstance(pick_answer, dict) else None
        )
        return _result(
            _promote(ranked, picked),
            excluded,
            AutoLenderDecision(
                picked_by=PICKED_BY_JEV,
                model=cached.model,
                confidence=confidence,
                probabilities=probabilities,
                owner_safe=owner_safe,
                cached=True,
                usage=cached.usage,
                jev_trace=_build_jev_trace(
                    status="cached",
                    cached=True,
                    model=cached.model,
                    jev_input=jev_input,
                    jev_output=cached.output,
                    guards=_build_jev_guards(
                        cfg=cfg,
                        pick_choice=pick_choice,
                        confidence=confidence,
                        probabilities=probabilities,
                        owner_safe=owner_safe,
                    ),
                ),
            ),
            on_decision,
            force_refresh=jev_bypass_cache,
        )

    if _breaker_open():
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(
                fallback_reason="circuit_open",
                jev_trace=_build_jev_trace(
                    status="skipped",
                    skip_reason="circuit_open",
                    model=jev.model,
                    jev_input=jev_input,
                    guards=_build_jev_guards(cfg=cfg),
                ),
            ),
            on_decision,
            force_refresh=jev_bypass_cache,
        )

    try:
        jev_decision = jev.decide(
            state=jev_input["state"],
            questions=jev_input["questions"],
        )
    except JevError as exc:
        _record_failure(jev_config)
        logger.warning("auto lender: jev call failed: %s", exc)
        msg = str(exc)
        if len(msg) > 200:
            msg = msg[:200]
        return _result(
            ranked,
            excluded,
            AutoLenderDecision(
                fallback_reason="jev_error",
                jev_trace=_build_jev_trace(
                    status="skipped",
                    skip_reason="jev_error",
                    error_message=msg,
                    model=jev.model,
                    jev_input=jev_input,
                    guards=_build_jev_guards(cfg=cfg),
                ),
            ),
            on_decision,
            force_refresh=jev_bypass_cache,
        )

    _record_success()
    jev_output = _serialize_jev_output(jev_decision)
    picked, reason, confidence, probabilities, owner_safe = _evaluate_jev(jev_decision, top, cfg)
    pick_answer = jev_decision.answer(PICK_QUESTION)
    pick_choice = pick_answer.choice if pick_answer else None
    guards = _build_jev_guards(
        cfg=cfg,
        pick_choice=pick_choice,
        confidence=confidence,
        probabilities=probabilities,
        owner_safe=owner_safe,
        fallback_reason=reason,
    )
    decision = AutoLenderDecision(
        picked_by=PICKED_BY_JEV if picked else PICKED_BY_ALGORITHM,
        fallback_reason=reason,
        model=jev_decision.model,
        confidence=confidence,
        probabilities=probabilities,
        owner_safe=owner_safe,
        usage=jev_decision.usage,
        jev_trace=_build_jev_trace(
            status="called",
            model=jev_decision.model,
            jev_input=jev_input,
            jev_output=jev_output,
            guards=guards,
        ),
    )
    if picked is None:
        return _result(ranked, excluded, decision, on_decision, force_refresh=jev_bypass_cache)

    _cache_put(
        key,
        _CachedJevPayload(
            account_id=picked,
            confidence=confidence,
            probabilities=probabilities,
            owner_safe=owner_safe,
            model=jev_decision.model,
            output=jev_output,
            usage=jev_decision.usage if isinstance(jev_decision.usage, dict) else {},
        ),
        ttl_seconds=cfg.auto_cache_seconds,
    )
    return _result(_promote(ranked, picked), excluded, decision, on_decision, force_refresh=jev_bypass_cache)
