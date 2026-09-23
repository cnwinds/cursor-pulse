from __future__ import annotations


DELIVERY_CURSOR_DIRECT = "cursor_direct"
DELIVERY_PROXY_ALIAS = "proxy_alias"
VALID_DELIVERY_MODES = frozenset({DELIVERY_CURSOR_DIRECT, DELIVERY_PROXY_ALIAS})

# pinned: one Cursor account for the life of the loan (manual, or self-service auto).
# pool: admin auto-assign. pka_ has no source account and rotates on the Credential Pool.
ROUTING_PINNED = "pinned"
ROUTING_POOL = "pool"

# 出借账号选择方式。manual = 发放时固定一把 Cursor Key；
# auto = 自助借 Key 在候选白名单内游走，或管理员自动分配走账号池轮换
# （后者同时写 routing_mode=pool，见 issue_pool_loan）。
# 单一真相来源；DDL 默认值（models.py / migrate.py）按存储层惯例内联字面量。
LENDER_MODE_MANUAL = "manual"
LENDER_MODE_AUTO = "auto"
VALID_LENDER_MODES = frozenset({LENDER_MODE_MANUAL, LENDER_MODE_AUTO})


def assignment_mode_label(
    *,
    delivery_mode: str | None,
    lender_mode: str | None,
    routing_mode: str | None,
) -> str:
    """列表/通知用分配方式文案：Cursor Key、指定账号、自动分配。"""
    mode = (delivery_mode or DELIVERY_CURSOR_DIRECT).strip()
    if mode != DELIVERY_PROXY_ALIAS:
        return "Cursor Key"
    if (routing_mode or ROUTING_PINNED).strip() == ROUTING_POOL:
        return "自动分配"
    if (lender_mode or LENDER_MODE_MANUAL).strip() == LENDER_MODE_AUTO:
        return "自动分配"
    return "指定账号"


class KeyLoanError(ValueError):
    pass
