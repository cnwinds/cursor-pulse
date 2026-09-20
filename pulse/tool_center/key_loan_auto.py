"""Auto Lender 借用侧：发放选号 + 定期重评。

选号本身在 :mod:`pulse.tool_center.auto_lender`（硬过滤 + 算法分 + Jev）。本模块
只负责把它接到 Key Loan 生命周期上，并守住驻留窗口：账号绑定后
``loan_selection.min_switch_minutes`` 内不主动换绑。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AiAccount, KeyLoan
from pulse.tool_center.auto_lender import PICKED_BY_JEV, rank_lenders
from pulse.tool_center.key_loan_delivery import LENDER_MODE_AUTO
from pulse.tool_center.key_loan_lender import build_lender_candidates
from pulse.tool_center.quota_pool import quota_pool_for_model
from pulse.util.datetime_fmt import ensure_aware

logger = logging.getLogger(__name__)

# 该借用最近有过代理流量 → 视为在用，本轮不换绑
DEFAULT_ACTIVE_TRAFFIC_MINUTES = 10.0


def own_cursor_account_ids(
    session: Session, team_id: str, borrower_member_id: str | None
) -> set[str]:
    """借用人自己名下的 Cursor 账号：借用不借自己的号。"""
    if not borrower_member_id:
        return set()
    from pulse.tool_center.account_pick import filter_cursor_accounts
    from pulse.tool_center.repository import ToolCenterRepository

    repo = ToolCenterRepository(session, team_id)
    accounts = repo.get_primary_accounts_for_member(borrower_member_id)
    return {account.id for account in filter_cursor_accounts(accounts)}


def resolve_auto_lender(
    session: Session,
    team_id: str,
    *,
    borrower_member_id: str | None,
    model: str | None = None,
    loan_selection: LoanSelectionConfig | None = None,
    jev=None,
    jev_config=None,
    on_decision: Callable[[dict], None] | None = None,
    exclude_account_ids: set[str] | None = None,
    own_account_ids: set[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """按 Auto Lender 规则选出借账号。

    返回 ``{"best": 最优候选 payload 或 None, "decision": ..., "ranked": [...],
    "excluded": [...]}``。硬过滤（耗尽 / 覆盖时长 / 名额 / 主负责人保留）
    不可被 Jev 绕过；``ranked[0]`` 即最终选定账号。

    ``own_account_ids`` 由调用方传入时不再查询借用人名下账号，避免同一流程
    重复取数。
    """
    exclude = set(exclude_account_ids or ())
    if own_account_ids is not None:
        exclude |= own_account_ids
    else:
        exclude |= own_cursor_account_ids(session, team_id, borrower_member_id)
    candidates = build_lender_candidates(
        session, team_id, exclude_account_ids=exclude
    )
    board = rank_lenders(
        candidates,
        loan_selection=loan_selection,
        # 模型未知 → unknown：要求 auto 与 api 两桶都还有 Snapshot Headroom，
        # 与 Go snapshotQuotaOK 一致（借用没有「入池 OR」那条规则）
        pool=quota_pool_for_model(model),
        now=now,
        jev=jev,
        jev_config=jev_config,
        on_decision=on_decision,
    )
    ranked = board["ranked"]
    return {
        "best": ranked[0] if ranked else None,
        "decision": board["decision"],
        "ranked": ranked,
        "excluded": board["excluded"],
    }


def record_auto_lender_decision(session: Session, result: dict) -> None:
    """把一次 Auto Lender 决策写入 proxy_events（审计）。

    只记「确有决策价值」的情况：Jev 选中了账号，或出现了回落原因。纯算法分
    且无回落（如 auto 未开启）不写，避免池轮询/预览把事件表刷爆。调用方负责
    commit。
    """
    decision = (result or {}).get("decision") or {}
    picked_by = decision.get("picked_by")
    reason = decision.get("fallback_reason")
    if picked_by != PICKED_BY_JEV and not reason:
        return
    if reason == "auto_mode_off":
        return
    picked = next(
        (row for row in (result.get("ranked") or []) if row.get("picked")), None
    )
    detail = {
        "picked_by": picked_by,
        "fallback_reason": reason,
        "model": decision.get("model"),
        "confidence": decision.get("confidence"),
        "cached": decision.get("cached"),
        "account_id": (picked or {}).get("account_id"),
        "account_identifier": (picked or {}).get("account_identifier"),
        "probabilities": decision.get("probabilities") or {},
        "owner_safe": decision.get("owner_safe") or {},
    }
    try:
        from pulse.proxy.key_crud import record_event

        record_event(
            session,
            event_type="lender_auto_pick",
            detail=json.dumps(detail, ensure_ascii=False, sort_keys=True),
        )
    except Exception:
        logger.warning("auto lender: audit event failed", exc_info=True)


def reevaluate_auto_loans(
    session: Session,
    encryption_key: str,
    *,
    team_id: str,
    loan_selection: LoanSelectionConfig | None = None,
    jev=None,
    jev_config=None,
    cursor_client=None,
    on_decision: Callable[[dict], None] | None = None,
    now: datetime | None = None,
    active_traffic_minutes: float = DEFAULT_ACTIVE_TRAFFIC_MINUTES,
    switch_margin: float | None = None,
    commit: bool = True,
) -> dict:
    """重评 ``lender_mode=auto`` 的进行中借用，必要时换绑出借账号。

    跳过条件（任一命中即不动）：
    1. Auto Lender 未开启；
    2. 距 ``source_bound_at`` 未满 ``min_switch_minutes``（驻留窗口）；
    3. 该借用最近 ``active_traffic_minutes`` 内有代理流量（在用）；
    4. 新首选与当前账号分差未超过 ``switch_margin``。
    """
    cfg = loan_selection or LoanSelectionConfig()
    stats = {"checked": 0, "switched": 0, "skipped_dwell": 0, "skipped_traffic": 0, "skipped_no_gain": 0}
    if not cfg.auto_mode:
        return stats

    from pulse.tool_center.key_loan_issue import (
        finalize_reassign_old_remote_revoke,
        reassign_loan_source,
    )

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    margin = cfg.auto_switch_margin if switch_margin is None else switch_margin

    loans = list(
        session.scalars(
            select(KeyLoan)
            .join(AiAccount, KeyLoan.source_account_id == AiAccount.id)
            .where(
                AiAccount.team_id == team_id,
                KeyLoan.status == "active",
                KeyLoan.lender_mode == LENDER_MODE_AUTO,
            )
        )
    )
    if not loans:
        return stats

    from pulse.proxy.usage_queries import last_loan_usage_at

    last_usage = last_loan_usage_at(session, [loan.id for loan in loans])

    # 先做便宜的驻留 / 在用判断，只把真正要重评的借用拿去排名
    pending: list[KeyLoan] = []
    for loan in loans:
        stats["checked"] += 1
        bound_at = ensure_aware(loan.source_bound_at) or ensure_aware(loan.created_at)
        if bound_at is not None and cfg.min_switch_minutes > 0:
            minutes_bound = (now - bound_at).total_seconds() / 60.0
            if minutes_bound < cfg.min_switch_minutes:
                stats["skipped_dwell"] += 1
                continue
        used_at = ensure_aware(last_usage.get(loan.id))
        if used_at is not None and active_traffic_minutes > 0:
            minutes_idle = (now - used_at).total_seconds() / 60.0
            if minutes_idle < active_traffic_minutes:
                stats["skipped_traffic"] += 1
                continue
        pending.append(loan)

    if not pending:
        return stats

    # 同一借用人的候选集与排名相同：按借用人分组，一组只构建一次候选、
    # 最多问一次 Jev，避免逐笔重算与 N+1 外呼。
    grouped: dict[str | None, list[KeyLoan]] = {}
    for loan in pending:
        grouped.setdefault(loan.borrower_member_id, []).append(loan)

    for borrower_member_id, group in grouped.items():
        resolved = resolve_auto_lender(
            session,
            team_id,
            borrower_member_id=borrower_member_id,
            loan_selection=cfg,
            jev=jev,
            jev_config=jev_config,
            on_decision=on_decision,
            now=now,
        )
        best = resolved["best"]
        if not best:
            # 无候选也要落审计（决策里有回落原因），否则「为什么没换」不可追溯
            if commit:
                session.commit()
            continue
        decision = resolved["decision"]
        scores = {row["account_id"]: row["score"] for row in resolved["ranked"]}
        if commit:
            # 先落审计事件；本轮若无需换绑，事件也不会丢
            session.commit()

        for loan in group:
            if best["account_id"] == loan.source_account_id:
                continue

            # 分差比较与 best 同源（同 pool、同算法分），避免拿 Jev 名次比算法分。
            # Jev 明确选出别的账号时跳过该闸门：规格 P2-11 要求「分差 >
            # switch_margin 才换绑」，但同一规格 P1 又要求「LLM 直接出结果、算法分
            # 只作保底」——按字面实现会让 Jev 只能选到算法分本来就更高的账号，
            # 主判形同废止。这里以「Jev 已过置信度/间隔/主负责人护栏」为准，
            # 闸门只约束算法保底路径。取舍见 docs/adr/0001。
            if decision.get("picked_by") != PICKED_BY_JEV:
                current_score = scores.get(loan.source_account_id)
                if (
                    current_score is not None
                    and (best["score"] - current_score) <= margin
                ):
                    stats["skipped_no_gain"] += 1
                    continue

            try:
                result = reassign_loan_source(
                    session,
                    encryption_key,
                    team_id=team_id,
                    loan_id=loan.id,
                    new_source_account_id=best["account_id"],
                    bound_by_member_id=loan.borrower_member_id or loan.source_account_id,
                    cursor_client=cursor_client,
                    loan_selection=cfg,
                )
            except Exception:
                logger.warning(
                    "auto lender: reassign loan %s failed", loan.id, exc_info=True
                )
                session.rollback()
                continue
            if commit:
                session.commit()
                finalize_reassign_old_remote_revoke(
                    session,
                    encryption_key,
                    result,
                    cursor_client=cursor_client,
                )
            stats["switched"] += 1
            logger.info(
                "auto lender: loan %s switched %s -> %s (decision=%s)",
                loan.id,
                result.get("old_source_account_identifier"),
                result.get("source_account_identifier"),
                decision.get("picked_by"),
            )
    return stats
