"""Auto Lender 借用侧：发放选号 + 审计。

选号本身在 :mod:`pulse.tool_center.auto_lender`（硬过滤 + 算法分 + Jev）。本模块
只负责把它接到 Key Loan 生命周期上。

**换号不在这里做**：自动分配借用的出借账号由代理在会话内游走——Pulse 在下发
授权时给出候选 primary 凭证白名单（见
:func:`pulse.proxy.pool_board.loan_candidate_credentials`），Go 侧在
``loan_alias`` 上按共享池的方式选号（sticky + Switch dwell + 按 Quota Pool）。
DB 层的定时重评与远端 Key 换绑已退休：那样每次换号都要新建/吊销一把 Cursor
Key，且粒度只能是分钟级，做不到「使用过程中灵活更换」。
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from sqlalchemy.orm import Session

from pulse.config import LoanSelectionConfig
from pulse.storage.models import AiAccount
from pulse.tool_center.auto_lender import PICKED_BY_JEV, rank_lenders
from pulse.tool_center.key_loan_delivery import LENDER_MODE_AUTO
from pulse.tool_center.key_loan_lender import build_lender_candidates
from pulse.tool_center.quota_pool import quota_pool_for_model

logger = logging.getLogger(__name__)



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


