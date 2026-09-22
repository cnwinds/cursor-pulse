from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AccountQuotaSnapshot
from pulse.tool_center.snapshot_headroom import (
    QuotaPoolKind,
    snapshot_has_any_pool_headroom,
    snapshot_quota_ok_for_pool,
)
from pulse.util.datetime_fmt import ensure_aware


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


def _cents_from_pct_used(limit_cents: int, pct_used: float) -> int:
    return round(limit_cents * max(0.0, 100.0 - pct_used) / 100.0)


def display_remaining_cents(snapshot: AccountQuotaSnapshot) -> int | None:
    """Total Snapshot Headroom in cents, from total_pct.

    planUsage.remaining（limit - includedSpend）常与 totalPercentUsed 不一致；
    有百分比时按 total_pct 从 limit 推算。
    """
    if snapshot.limit_cents <= 0:
        return snapshot.remaining_cents or None
    if snapshot.total_pct is not None:
        return _cents_from_pct_used(snapshot.limit_cents, snapshot.total_pct)
    return snapshot.remaining_cents or None


def display_api_remaining_cents(snapshot: AccountQuotaSnapshot) -> int | None:
    """API Quota Pool Snapshot Headroom in cents, from api_pct.

    Missing api_pct is unknown → None. Do not fall back to remaining_cents
    (that is included-total, not the API Quota Pool).
    """
    if snapshot.api_pct is None or snapshot.limit_cents <= 0:
        return None
    return _cents_from_pct_used(snapshot.limit_cents, snapshot.api_pct)


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
    """一个出借候选：最新配额快照 + 账号侧调参 + 驻留基准。"""

    snapshot: AccountQuotaSnapshot
    account_id: str
    account_identifier: str
    renews_on: date | None = None
    active_loans: int = 0
    primary_member_name: str | None = None
    score_adjust: float | None = None
    # 主负责人保留量（账号级 proxy_reserve_pct）；None 时用配置默认值
    reserve_pct: float | None = None
    # 该账号最近一次出借绑定时刻；auto 模式的驻留窗口基准
    bound_at: datetime | None = None


def lender_deadline(cycle_end: date, renews_on: date | None) -> date:
    """额度作废截止日：账期重置日与订阅到期日取先到者。

    打分侧使用，数据源为快照 cycle_end；回收/展示侧见
    key_loans.account_loan_deadline（数据源 account.usage_resets_on，
    与 cycle_end 同源自 Cursor billingCycleEnd）。
    """
    if renews_on and renews_on < cycle_end:
        return renews_on
    return cycle_end


def _ensure_aware(dt: datetime) -> datetime:
    """Naive → UTC（复用共享实现；本模块调用点保证非 None）。"""
    return ensure_aware(dt)  # type: ignore[return-value]


def lender_deadline_at(
    cycle_end: date,
    renews_on: date | None,
    *,
    cycle_end_at: datetime | None = None,
) -> datetime:
    """作废截止时刻：优先 Cursor billingCycleEnd 精确时间；无则 UTC 日终。

    renews_on 早于 cycle_end 时只有日期、无时钟，仍按该日 UTC 23:59:59。
    """
    deadline = lender_deadline(cycle_end, renews_on)
    if renews_on is not None and renews_on < cycle_end:
        return datetime.combine(deadline, time(23, 59, 59), tzinfo=timezone.utc)
    if cycle_end_at is not None:
        return _ensure_aware(cycle_end_at)
    return datetime.combine(deadline, time(23, 59, 59), tzinfo=timezone.utc)


def hours_until_deadline(
    deadline: date | datetime, now: datetime | None = None
) -> float:
    """距作废的小时数。

    deadline 为 datetime 时用精确时刻；仅为 date 时回退 UTC 当天 23:59:59
    （兼容无 cycle_end_at 的旧快照）。
    """
    now = _ensure_aware(now or datetime.now(timezone.utc))
    if isinstance(deadline, datetime):
        end = _ensure_aware(deadline)
    else:
        end = datetime.combine(deadline, time(23, 59, 59), tzinfo=timezone.utc)
    return max((end - now).total_seconds() / 3600.0, 0.0)


def projected_surplus_cents(
    snapshot: AccountQuotaSnapshot, days_to_deadline: float, today: date | None = None
) -> float:
    """号主自身消耗到 deadline 也用不完的额度（cents）；无法推算时为 0。

    days_to_deadline 可为小数（小时精度 = hours/24）。
    有 total_pct 时按 Snapshot Headroom 推算（与 display_remaining_cents 一致）；
    planUsage.remaining 常与 totalPercentUsed 不一致，会压低空闲账号的余量。
    """
    today = today or date.today()
    elapsed = max((today - snapshot.cycle_start).days, 1)
    if snapshot.total_pct is not None and snapshot.limit_cents > 0:
        daily_pct = snapshot.total_pct / elapsed
        surplus_pct = max(100.0 - (snapshot.total_pct + daily_pct * days_to_deadline), 0.0)
        return round(surplus_pct / 100.0 * snapshot.limit_cents, 2)
    if snapshot.remaining_cents > 0:
        daily_burn = snapshot.used_cents / elapsed
        return round(max(snapshot.remaining_cents - daily_burn * days_to_deadline, 0.0), 2)
    return 0.0


def _pool_pct(snapshot: AccountQuotaSnapshot, pool: QuotaPoolKind) -> float | None:
    """该 Quota Pool 的使用百分比。

    ``unknown`` 取两桶较高者：选号时两桶都要有余量，用更紧张的那个评估才安全。
    """
    if pool == "auto":
        return snapshot.auto_pct
    if pool == "api":
        return snapshot.api_pct
    known = [p for p in (snapshot.auto_pct, snapshot.api_pct) if p is not None]
    if known:
        return max(known)
    return None


def pool_headroom_pct(
    snapshot: AccountQuotaSnapshot, pool: QuotaPoolKind | None
) -> float:
    """按池的 Snapshot Headroom；pool 为 None 或该桶缺失时回落 total。"""
    if pool is None:
        return remaining_headroom_pct(snapshot)
    pct = _pool_pct(snapshot, pool)
    if pct is None:
        return remaining_headroom_pct(snapshot)
    return round(max(100.0 - pct, 0.0), 2)


def pool_surplus_cents(
    snapshot: AccountQuotaSnapshot,
    pool: QuotaPoolKind | None,
    days_to_deadline: float,
    today: date | None = None,
) -> float:
    """按池推算号主到 deadline 也用不完的额度（cents）。

    Cursor 快照只有每桶百分比、没有每桶额度，因此借用 included 总额按比例折算。
    候选之间比较时这是单调变换，不改变排序；绝对值仅供展示。
    """
    if pool is None:
        return projected_surplus_cents(snapshot, days_to_deadline, today)
    pct = _pool_pct(snapshot, pool)
    if pct is None or snapshot.limit_cents <= 0:
        return projected_surplus_cents(snapshot, days_to_deadline, today)
    today = today or date.today()
    elapsed = max((today - snapshot.cycle_start).days, 1)
    daily_pct = pct / elapsed
    surplus_pct = max(100.0 - (pct + daily_pct * days_to_deadline), 0.0)
    return round(surplus_pct / 100.0 * snapshot.limit_cents, 2)


def owner_reserve_ok(
    snapshot: AccountQuotaSnapshot,
    pool: QuotaPoolKind | None,
    reserve_pct: float | None,
    days_to_deadline: float,
    today: date | None = None,
) -> bool:
    """主负责人保留量是否仍安全。

    以号主当前消耗速率外推到作废日：预计占用超过 ``100 - reserve_pct`` 即视为
    借用会侵占主负责人预留。``reserve_pct`` 为 None/<=0 时不设限。
    """
    if not reserve_pct or reserve_pct <= 0:
        return True
    pct = _pool_pct(snapshot, pool) if pool is not None else None
    if pct is None:
        pct = (
            snapshot.total_pct
            if snapshot.total_pct is not None
            else quota_progress(snapshot) * 100.0
        )
    today = today or date.today()
    elapsed = max((today - snapshot.cycle_start).days, 1)
    daily_pct = pct / elapsed
    projected_pct = pct + daily_pct * days_to_deadline
    return projected_pct <= (100.0 - reserve_pct)


def effective_reserve_pct(
    cand: "LenderCandidate", cfg: LoanSelectionConfig
) -> float | None:
    """生效的主负责人保留量：账号级设置优先，否则用配置默认值。"""
    return cand.reserve_pct if cand.reserve_pct is not None else cfg.owner_reserve_pct


def switch_recency_factor(
    bound_at: datetime | None,
    min_switch_minutes: float,
    now: datetime,
) -> float:
    """[0,1]：刚绑定/刚切走为 0，已超过驻留窗口为 1。"""
    if bound_at is None or min_switch_minutes <= 0:
        return 1.0
    minutes = (now - _ensure_aware(bound_at)).total_seconds() / 60.0
    if minutes >= min_switch_minutes:
        return 1.0
    return round(max(minutes, 0.0) / min_switch_minutes, 4)


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


def digestion_urgency(
    surplus: float,
    hours: float,
    *,
    deadline_power: float,
    hourly_power: bool,
) -> float:
    """待消化压力：余量 / 剩余时间^power。power>1 时更优先快到期账号。"""
    if hourly_power:
        hour_floor = 1.0
        return surplus / max(hours**deadline_power, hour_floor**deadline_power)
    days = max(hours / 24.0, 1 / 24)
    day_floor = 1 / 24
    return surplus / max(days**deadline_power, day_floor**deadline_power)


@dataclass(frozen=True)
class _ScoringProfile:
    weight_urgency: float
    weight_surplus: float
    weight_load: float
    weight_freshness: float
    weight_headroom: float
    deadline_power: float
    hourly_power: bool


def _scoring_profile(cfg: LoanSelectionConfig, *, enforce_loan_cap: bool) -> _ScoringProfile:
    if enforce_loan_cap:
        return _ScoringProfile(
            weight_urgency=cfg.weight_urgency,
            weight_surplus=cfg.weight_surplus,
            weight_load=cfg.weight_load,
            weight_freshness=cfg.weight_freshness,
            weight_headroom=0.0,
            deadline_power=1.0,
            hourly_power=False,
        )
    return _ScoringProfile(
        weight_urgency=cfg.proxy_weight_urgency,
        weight_surplus=cfg.proxy_weight_surplus,
        weight_load=0.0,
        weight_freshness=cfg.proxy_weight_freshness,
        weight_headroom=cfg.proxy_weight_headroom,
        deadline_power=cfg.proxy_deadline_power,
        hourly_power=True,
    )


def _hard_filter_reason(
    cand: LenderCandidate,
    cfg: LoanSelectionConfig,
    today: date,
    now: datetime,
    *,
    enforce_loan_cap: bool = True,
    exclude_at_loan_cap: bool | None = None,
    pool: QuotaPoolKind | None = None,
    reserve_ok: bool | None = None,
) -> str | None:
    """返回排除原因码；通过硬过滤则 None。

    借 Key（enforce_loan_cap=True）用 total_pct / burn_rate 的 exhausted。
    代理入池（False）用 Snapshot Headroom：两桶都满才 exhausted，与 Go
    per-bucket 选择对齐（入池 OR，请求时按桶过滤）。
    exclude_at_loan_cap 为 None 时跟随 enforce_loan_cap；管理员选账号列表
    可传 False，只放开人数上限、仍走借用路径打分。
    pool 非空时额外要求该 Quota Pool 仍有 Snapshot Headroom（与 Go
    snapshotQuotaOK 一致），并按池判断主负责人保留量。
    reserve_ok 由调用方预先算好时传入，避免同一候选重复求值。
    """
    analysis = analyze_burn_rate(cand.snapshot, today)
    if enforce_loan_cap:
        if analysis.status == "exhausted":
            return "exhausted"
    elif not snapshot_has_any_pool_headroom(
        auto_pct=cand.snapshot.auto_pct,
        api_pct=cand.snapshot.api_pct,
    ):
        return "exhausted"
    if pool is not None and not snapshot_quota_ok_for_pool(
        pool,
        auto_pct=cand.snapshot.auto_pct,
        api_pct=cand.snapshot.api_pct,
    ):
        return "exhausted"
    if analysis.exhausts_before_reset:
        # Pool intake: total-burn "already exhausted" projection must not
        # override per-bucket Snapshot Headroom (OR intake rule).
        if enforce_loan_cap or analysis.status != "exhausted":
            return "exhausts_before_reset"
    should_exclude_cap = (
        enforce_loan_cap if exclude_at_loan_cap is None else exclude_at_loan_cap
    )
    if should_exclude_cap and cand.active_loans >= cfg.max_active_loans_per_account:
        return "loan_cap"
    deadline_at = lender_deadline_at(
        cand.snapshot.cycle_end,
        cand.renews_on,
        cycle_end_at=cand.snapshot.cycle_end_at,
    )
    hours = hours_until_deadline(deadline_at, now)
    if hours <= cfg.min_coverage_hours:
        return "coverage_too_short"
    if reserve_ok is None:
        reserve_ok = owner_reserve_ok(
            cand.snapshot, pool, effective_reserve_pct(cand, cfg), hours / 24.0, today
        )
    if not reserve_ok:
        return "owner_reserve"
    return None


def _pool_view(
    snapshot: AccountQuotaSnapshot,
    *,
    pool: QuotaPoolKind | None,
    headroom: float,
    surplus: float,
    reserve_ok: bool,
    reserve_pct: float | None,
    recency_factor: float,
    bound_at: datetime | None,
    now: datetime,
) -> dict:
    """按池指标 + 驻留状态，供打分 payload / UI / Jev state 复用。"""
    minutes_since_switch = None
    if bound_at is not None:
        minutes_since_switch = round(
            max((now - ensure_aware(bound_at)).total_seconds() / 60.0, 0.0), 1
        )
    return {
        "pool": pool,
        "pool_headroom_pct": headroom,
        "pool_surplus_cents": surplus,
        "owner_reserve_ok": reserve_ok,
        "reserve_pct": None if reserve_pct is None else round(reserve_pct, 4),
        "minutes_since_switch": minutes_since_switch,
        "recency_factor": recency_factor,
    }


def _score_payload(
    cand: LenderCandidate,
    *,
    analysis: BurnRateAnalysis,
    deadline: date,
    deadline_at: datetime,
    days: int,
    hours: float,
    surplus: float,
    urgency: float,
    freshness: float,
    score: float,
    computed_score: float,
    pool_view: dict | None = None,
) -> dict:
    snapshot = cand.snapshot
    adjust = cand.score_adjust
    payload = {
        "account_id": cand.account_id,
        "account_identifier": cand.account_identifier,
        "primary_member_name": cand.primary_member_name,
        "score": round(score, 4),
        "computed_score": round(computed_score, 4),
        "score_adjust": None if adjust is None else round(adjust, 4),
        "deadline": deadline.isoformat(),
        "deadline_at": deadline_at.isoformat(),
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
    if pool_view:
        payload.update(pool_view)
    return payload


def _rank_passing_candidates(
    candidates: list[LenderCandidate],
    cfg: LoanSelectionConfig,
    today: date,
    now: datetime,
    *,
    enforce_loan_cap: bool = True,
    exclude_at_loan_cap: bool | None = None,
    pool: QuotaPoolKind | None = None,
) -> tuple[list[dict], list[dict]]:
    """硬过滤 + 打分。返回 (ranked_payloads, excluded_payloads)。

    enforce_loan_cap=False 时：不按在借人数硬过滤，打分忽略 L（load）因子；
    exhausted 改为两桶 Snapshot Headroom 都满才排除（见 snapshot_has_any_pool_headroom）。
    exclude_at_loan_cap=False 且 enforce_loan_cap=True：仍用借用打分，但不因
    在借人数达上限排除。
    pool 非空时按该 Quota Pool 取余量/空闲额度，并按池判断主负责人保留量。
    """
    rows: list[dict] = []
    excluded: list[dict] = []
    profile = _scoring_profile(cfg, enforce_loan_cap=enforce_loan_cap)

    for cand in candidates:
        deadline_at = lender_deadline_at(
            cand.snapshot.cycle_end,
            cand.renews_on,
            cycle_end_at=cand.snapshot.cycle_end_at,
        )
        # 保留量只算一次：硬过滤与打分行共用同一结果
        reserve_pct = effective_reserve_pct(cand, cfg)
        reserve_ok = owner_reserve_ok(
            cand.snapshot,
            pool,
            reserve_pct,
            hours_until_deadline(deadline_at, now) / 24.0,
            today,
        )
        reason = _hard_filter_reason(
            cand,
            cfg,
            today,
            now,
            enforce_loan_cap=enforce_loan_cap,
            exclude_at_loan_cap=exclude_at_loan_cap,
            pool=pool,
            reserve_ok=reserve_ok,
        )
        if reason is not None:
            analysis = analyze_burn_rate(cand.snapshot, today)
            deadline = lender_deadline(cand.snapshot.cycle_end, cand.renews_on)
            adjust = cand.score_adjust
            excluded.append(
                {
                    "account_id": cand.account_id,
                    "account_identifier": cand.account_identifier,
                    "primary_member_name": cand.primary_member_name,
                    "reason": reason,
                    "active_loans": cand.active_loans,
                    "status": analysis.status,
                    "deadline": deadline.isoformat(),
                    "deadline_at": deadline_at.isoformat(),
                    "hours_to_deadline": round(hours_until_deadline(deadline_at, now), 1),
                    "renews_on": cand.renews_on.isoformat() if cand.renews_on else None,
                    "remaining_headroom_pct": analysis.remaining_headroom_pct,
                    "total_pct": cand.snapshot.total_pct,
                    "auto_pct": cand.snapshot.auto_pct,
                    "api_pct": cand.snapshot.api_pct,
                    "score_adjust": None if adjust is None else round(adjust, 4),
                    "reserve_pct": None if reserve_pct is None else round(reserve_pct, 4),
                    "owner_reserve_ok": reserve_ok,
                    "pool": pool,
                }
            )
            continue
        snapshot = cand.snapshot
        analysis = analyze_burn_rate(snapshot, today)
        deadline = lender_deadline(snapshot.cycle_end, cand.renews_on)
        days = (deadline - today).days
        hours = round(hours_until_deadline(deadline_at, now), 1)
        surplus = pool_surplus_cents(snapshot, pool, hours / 24.0, today)
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
                "deadline_at": deadline_at,
                "days": days,
                "hours": hours,
                "surplus": surplus,
                "headroom": pool_headroom_pct(snapshot, pool),
                "urgency": digestion_urgency(
                    surplus,
                    hours,
                    deadline_power=profile.deadline_power,
                    hourly_power=profile.hourly_power,
                ),
                "load_factor": load_factor,
                "freshness": snapshot_freshness(
                    snapshot, cfg.freshness_full_penalty_hours, now
                ),
                "recency": switch_recency_factor(
                    cand.bound_at, cfg.min_switch_minutes, now
                ),
                "reserve_pct": reserve_pct,
                "reserve_ok": reserve_ok,
            }
        )

    if not rows:
        return [], excluded

    u_norm = _min_max([row["urgency"] for row in rows])
    s_norm = _min_max([row["surplus"] for row in rows])
    h_norm = _min_max([row["headroom"] for row in rows])
    ranked: list[tuple[float, float, dict]] = []
    for idx, row in enumerate(rows):
        score = (
            profile.weight_urgency * u_norm[idx]
            + profile.weight_surplus * s_norm[idx]
            + profile.weight_load * row["load_factor"]
            + profile.weight_headroom * h_norm[idx]
            + profile.weight_freshness * row["freshness"]
        )
        cand: LenderCandidate = row["candidate"]
        computed_score = score
        if cand.score_adjust is not None:
            score = computed_score + cand.score_adjust
        # 驻留窗口内降权：避免同一借用人在账号之间来回抖动
        score -= cfg.recency_penalty * (1.0 - row["recency"])
        pool_view = _pool_view(
            cand.snapshot,
            pool=pool,
            headroom=row["headroom"],
            surplus=row["surplus"],
            reserve_ok=row["reserve_ok"],
            reserve_pct=row["reserve_pct"],
            recency_factor=row["recency"],
            bound_at=cand.bound_at,
            now=now,
        )
        ranked.append(
            (
                score,
                row["hours"],
                _score_payload(
                    cand,
                    analysis=row["analysis"],
                    deadline=row["deadline"],
                    deadline_at=row["deadline_at"],
                    days=row["days"],
                    hours=row["hours"],
                    surplus=row["surplus"],
                    urgency=row["urgency"],
                    freshness=row["freshness"],
                    score=score,
                    computed_score=computed_score,
                    pool_view=pool_view,
                ),
            )
        )
    ranked.sort(
        key=lambda x: (
            -x[0],
            x[1],
            -x[2]["surplus_cents"],
            -x[2]["remaining_headroom_pct"],
            x[2]["account_id"],
        )
    )
    return [item for _, _, item in ranked], excluded


def recommend_lenders(
    candidates: list[LenderCandidate],
    today: date | None = None,
    *,
    loan_selection: LoanSelectionConfig | None = None,
    now: datetime | None = None,
    enforce_loan_cap: bool = True,
    exclude_at_loan_cap: bool | None = None,
    pool: QuotaPoolKind | None = None,
) -> list[dict]:
    """硬过滤后按待消化压力排序。

    借用路径：urgency ≈ 余量/剩余天数；U/S 池内归一化后加权。
    代理池路径（enforce_loan_cap=False）：
    - urgency = 余量/剩余小时^proxy_deadline_power（快到期优先消化，减少周期末浪费）
    - surplus = projected_surplus_cents 归一化（Snapshot Headroom 推算的空闲余量，多者优先）
    - headroom = remaining_headroom_pct 归一化（余量紧张的留给主使用人）
    - score_adjust 非空时加在算法综合分上再排序（微调，不绕过硬过滤）
    pool 非空时上述余量/空闲额度按该 Quota Pool 计算。
    同分按 hours_to_deadline 升序、surplus_cents 降序、account_id 打平。
    """
    cfg = loan_selection or LoanSelectionConfig()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if today is None:
        today = now.date()
    ranked, _ = _rank_passing_candidates(
        candidates,
        cfg,
        today,
        now,
        enforce_loan_cap=enforce_loan_cap,
        exclude_at_loan_cap=exclude_at_loan_cap,
        pool=pool,
    )
    return ranked


def explain_lender_selection(
    candidates: list[LenderCandidate],
    today: date | None = None,
    *,
    loan_selection: LoanSelectionConfig | None = None,
    now: datetime | None = None,
    enforce_loan_cap: bool = True,
    exclude_at_loan_cap: bool | None = None,
    pool: QuotaPoolKind | None = None,
) -> dict:
    """与 recommend_lenders 同源打分，额外返回硬过滤排除项。"""
    cfg = loan_selection or LoanSelectionConfig()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if today is None:
        today = now.date()
    ranked, excluded = _rank_passing_candidates(
        candidates,
        cfg,
        today,
        now,
        enforce_loan_cap=enforce_loan_cap,
        exclude_at_loan_cap=exclude_at_loan_cap,
        pool=pool,
    )
    return {"ranked": ranked, "excluded": excluded}
