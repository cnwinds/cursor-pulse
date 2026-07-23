from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AccountQuotaSnapshot


@dataclass
class BurnRateAnalysis:
    quota_progress: float
    projected_exhaustion_date: date | None
    exhausts_before_reset: bool
    status: str
    days_until_reset: int
    remaining_headroom_pct: float
    api_limit_usd: float | None


WARNING_TOTAL_PCT = 80.0


def _cycle_days(snapshot: AccountQuotaSnapshot) -> int:
    return max((snapshot.cycle_end - snapshot.cycle_start).days, 1)


def quota_progress(snapshot: AccountQuotaSnapshot) -> float:
    """与 Cursor Dashboard 一致：优先使用 planUsage.totalPercentUsed。"""
    if snapshot.total_pct is not None:
        return round(snapshot.total_pct / 100.0, 4)
    if snapshot.limit_cents <= 0:
        return 0.0
    return round(snapshot.used_cents / snapshot.limit_cents, 4)


def remaining_headroom_pct(snapshot: AccountQuotaSnapshot) -> float:
    if snapshot.total_pct is not None:
        return round(max(100.0 - snapshot.total_pct, 0.0), 2)
    prog = quota_progress(snapshot)
    return round(max((1.0 - prog) * 100.0, 0.0), 2)


def api_limit_usd(snapshot: AccountQuotaSnapshot) -> float | None:
    if snapshot.limit_cents > 0:
        return round(snapshot.limit_cents / 100.0, 2)
    return None


def projected_exhaustion_date(
    snapshot: AccountQuotaSnapshot, today: date | None = None
) -> date | None:
    today = today or date.today()
    if snapshot.total_pct is not None:
        if snapshot.total_pct >= 100:
            return today
        elapsed = max((today - snapshot.cycle_start).days, 1)
        daily_pct = snapshot.total_pct / elapsed
        if daily_pct <= 0:
            return None
        days_left = (100.0 - snapshot.total_pct) / daily_pct
        return today + timedelta(days=int(days_left))

    if snapshot.remaining_cents <= 0:
        return today
    elapsed = max((today - snapshot.cycle_start).days, 1)
    daily_burn = snapshot.used_cents / elapsed
    if daily_burn <= 0:
        return None
    days_left = snapshot.remaining_cents / daily_burn
    return today + timedelta(days=int(days_left))


def analyze_burn_rate(
    snapshot: AccountQuotaSnapshot,
    today: date | None = None,
    *,
    warning_total_pct: float = WARNING_TOTAL_PCT,
) -> BurnRateAnalysis:
    today = today or date.today()
    q_prog = quota_progress(snapshot)
    total_pct = snapshot.total_pct if snapshot.total_pct is not None else q_prog * 100.0
    projected = projected_exhaustion_date(snapshot, today)
    exhausts_before = projected is not None and projected < snapshot.cycle_end
    days_until_reset = max((snapshot.cycle_end - today).days, 0)
    headroom = remaining_headroom_pct(snapshot)

    if snapshot.total_pct is not None:
        exhausted = snapshot.total_pct >= 100
    else:
        exhausted = snapshot.remaining_cents <= 0 or q_prog >= 1.0

    if exhausted:
        status = "exhausted"
    elif total_pct >= warning_total_pct or exhausts_before:
        status = "warning"
    else:
        status = "healthy"

    return BurnRateAnalysis(
        quota_progress=q_prog,
        projected_exhaustion_date=projected,
        exhausts_before_reset=exhausts_before,
        status=status,
        days_until_reset=days_until_reset,
        remaining_headroom_pct=headroom,
        api_limit_usd=api_limit_usd(snapshot),
    )


@dataclass
class LenderCandidate:
    snapshot: AccountQuotaSnapshot
    account_id: str
    account_identifier: str
    renews_on: date | None = None
    active_loans: int = 0


def lender_deadline(cycle_end: date, renews_on: date | None) -> date:
    """额度作废截止日：账期重置日与订阅到期日取先到者。

    打分侧使用，数据源为快照 cycle_end；回收/展示侧见
    key_loans.account_loan_deadline（数据源 account.usage_resets_on，
    与 cycle_end 同源自 Cursor billingCycleEnd）。
    """
    if renews_on and renews_on < cycle_end:
        return renews_on
    return cycle_end


def hours_until_deadline(deadline: date, now: datetime | None = None) -> float:
    """距作废的小时数（deadline 日期按 UTC 当天 23:59:59 计）。"""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    end_of_day = datetime.combine(deadline, time(23, 59, 59), tzinfo=timezone.utc)
    return max((end_of_day - now).total_seconds() / 3600.0, 0.0)


def projected_surplus_cents(
    snapshot: AccountQuotaSnapshot, days_to_deadline: float, today: date | None = None
) -> float:
    """号主自身消耗到 deadline 也用不完的额度（cents）；无法推算时为 0。

    days_to_deadline 可为小数（小时精度 = hours/24）。
    """
    today = today or date.today()
    elapsed = max((today - snapshot.cycle_start).days, 1)
    if snapshot.remaining_cents > 0:
        daily_burn = snapshot.used_cents / elapsed
        return round(max(snapshot.remaining_cents - daily_burn * days_to_deadline, 0.0), 2)
    if snapshot.total_pct is not None and snapshot.limit_cents > 0:
        daily_pct = snapshot.total_pct / elapsed
        surplus_pct = max(100.0 - (snapshot.total_pct + daily_pct * days_to_deadline), 0.0)
        return round(surplus_pct / 100.0 * snapshot.limit_cents, 2)
    return 0.0


def snapshot_freshness(
    snapshot: AccountQuotaSnapshot,
    full_penalty_hours: float,
    now: datetime | None = None,
) -> float:
    """[0,1]：刚同步为 1，age ≥ full_penalty_hours 为 0；尺度 ≤ 0 时恒为 1。"""
    if full_penalty_hours <= 0:
        return 1.0
    now = now or datetime.now(timezone.utc)
    captured = snapshot.captured_at
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=timezone.utc)
    age_hours = max((now - captured).total_seconds() / 3600.0, 0.0)
    return round(max(1.0 - age_hours / full_penalty_hours, 0.0), 4)


def _min_max(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [1.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _hard_filter_reason(
    cand: LenderCandidate,
    cfg: LoanSelectionConfig,
    today: date,
    now: datetime,
    *,
    enforce_loan_cap: bool = True,
) -> str | None:
    """返回排除原因码；通过硬过滤则 None。"""
    analysis = analyze_burn_rate(cand.snapshot, today)
    if analysis.status == "exhausted":
        return "exhausted"
    if analysis.exhausts_before_reset:
        return "exhausts_before_reset"
    if enforce_loan_cap and cand.active_loans >= cfg.max_active_loans_per_account:
        return "loan_cap"
    deadline = lender_deadline(cand.snapshot.cycle_end, cand.renews_on)
    hours = hours_until_deadline(deadline, now)
    if hours <= cfg.min_coverage_hours:
        return "coverage_too_short"
    return None


def _score_payload(
    cand: LenderCandidate,
    *,
    analysis: BurnRateAnalysis,
    deadline: date,
    days: int,
    hours: float,
    surplus: float,
    urgency: float,
    freshness: float,
    score: float,
) -> dict:
    snapshot = cand.snapshot
    return {
        "account_id": cand.account_id,
        "account_identifier": cand.account_identifier,
        "score": round(score, 4),
        "deadline": deadline.isoformat(),
        "days_to_deadline": days,
        "hours_to_deadline": hours,
        "renews_on": cand.renews_on.isoformat() if cand.renews_on else None,
        "surplus_cents": surplus,
        "urgency_cents_per_day": round(urgency, 2),
        "active_loans": cand.active_loans,
        "snapshot_freshness": freshness,
        "remaining_headroom_pct": analysis.remaining_headroom_pct,
        "total_pct": snapshot.total_pct,
        "auto_pct": snapshot.auto_pct,
        "api_pct": snapshot.api_pct,
        "api_limit_usd": analysis.api_limit_usd,
        "days_until_reset": analysis.days_until_reset,
        "status": analysis.status,
        "cycle_start": snapshot.cycle_start.isoformat(),
        "cycle_end": snapshot.cycle_end.isoformat(),
    }


def _rank_passing_candidates(
    candidates: list[LenderCandidate],
    cfg: LoanSelectionConfig,
    today: date,
    now: datetime,
    *,
    enforce_loan_cap: bool = True,
) -> tuple[list[dict], list[dict]]:
    """硬过滤 + 打分。返回 (ranked_payloads, excluded_payloads)。

    enforce_loan_cap=False 时：不按在借人数硬过滤，且打分忽略 L（load）因子。
    """
    rows: list[dict] = []
    excluded: list[dict] = []
    for cand in candidates:
        reason = _hard_filter_reason(
            cand, cfg, today, now, enforce_loan_cap=enforce_loan_cap
        )
        if reason is not None:
            analysis = analyze_burn_rate(cand.snapshot, today)
            deadline = lender_deadline(cand.snapshot.cycle_end, cand.renews_on)
            excluded.append(
                {
                    "account_id": cand.account_id,
                    "account_identifier": cand.account_identifier,
                    "reason": reason,
                    "active_loans": cand.active_loans,
                    "status": analysis.status,
                    "deadline": deadline.isoformat(),
                    "hours_to_deadline": round(hours_until_deadline(deadline, now), 1),
                    "renews_on": cand.renews_on.isoformat() if cand.renews_on else None,
                    "remaining_headroom_pct": analysis.remaining_headroom_pct,
                    "total_pct": cand.snapshot.total_pct,
                }
            )
            continue
        snapshot = cand.snapshot
        analysis = analyze_burn_rate(snapshot, today)
        deadline = lender_deadline(snapshot.cycle_end, cand.renews_on)
        days = (deadline - today).days
        hours = round(hours_until_deadline(deadline, now), 1)
        surplus = projected_surplus_cents(snapshot, hours / 24.0, today)
        if enforce_loan_cap:
            load_factor = 1.0 - cand.active_loans / max(
                cfg.max_active_loans_per_account, 1
            )
        else:
            load_factor = 1.0
        rows.append(
            {
                "candidate": cand,
                "analysis": analysis,
                "deadline": deadline,
                "days": days,
                "hours": hours,
                "surplus": surplus,
                "urgency": surplus / max(hours / 24.0, 1 / 24),
                "load_factor": load_factor,
                "freshness": snapshot_freshness(
                    snapshot, cfg.freshness_full_penalty_hours, now
                ),
            }
        )

    if not rows:
        return [], excluded

    u_norm = _min_max([row["urgency"] for row in rows])
    s_norm = _min_max([row["surplus"] for row in rows])
    ranked: list[tuple[float, dict]] = []
    for idx, row in enumerate(rows):
        score = (
            cfg.weight_urgency * u_norm[idx]
            + cfg.weight_surplus * s_norm[idx]
            + cfg.weight_load * row["load_factor"]
            + cfg.weight_freshness * row["freshness"]
        )
        cand: LenderCandidate = row["candidate"]
        ranked.append(
            (
                score,
                _score_payload(
                    cand,
                    analysis=row["analysis"],
                    deadline=row["deadline"],
                    days=row["days"],
                    hours=row["hours"],
                    surplus=row["surplus"],
                    urgency=row["urgency"],
                    freshness=row["freshness"],
                    score=score,
                ),
            )
        )
    ranked.sort(key=lambda x: (-x[0], x[1]["account_id"]))
    return [item for _, item in ranked], excluded


def recommend_lenders(
    candidates: list[LenderCandidate],
    today: date | None = None,
    *,
    loan_selection: LoanSelectionConfig | None = None,
    now: datetime | None = None,
    enforce_loan_cap: bool = True,
) -> list[dict]:
    """硬过滤后按 urgency（单位时间待消化额度）主导的加权分排序。

    硬过滤：exhausted / 号主自身将耗尽 / 在借达上限（可关）/ 距作废 ≤ min_coverage_hours
    （默认 1 小时；大于 1 小时即可借）。urgency 按小时精度计权，时间越短权重越大。
    U（urgency）、S（surplus）池内 min-max 归一；L（在借人数）、F（快照新鲜度）
    本身即 [0,1] 绝对值直接入分。打平按 account_id 字典序保证确定性。
    """
    cfg = loan_selection or LoanSelectionConfig()
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    ranked, _ = _rank_passing_candidates(
        candidates, cfg, today, now, enforce_loan_cap=enforce_loan_cap
    )
    return ranked


def explain_lender_selection(
    candidates: list[LenderCandidate],
    today: date | None = None,
    *,
    loan_selection: LoanSelectionConfig | None = None,
    now: datetime | None = None,
    enforce_loan_cap: bool = True,
) -> dict:
    """与 recommend_lenders 同源打分，额外返回硬过滤排除项。"""
    cfg = loan_selection or LoanSelectionConfig()
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    ranked, excluded = _rank_passing_candidates(
        candidates, cfg, today, now, enforce_loan_cap=enforce_loan_cap
    )
    return {"ranked": ranked, "excluded": excluded}
