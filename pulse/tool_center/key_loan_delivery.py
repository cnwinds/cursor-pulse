from __future__ import annotations


DELIVERY_CURSOR_DIRECT = "cursor_direct"
DELIVERY_PROXY_ALIAS = "proxy_alias"
VALID_DELIVERY_MODES = frozenset({DELIVERY_CURSOR_DIRECT, DELIVERY_PROXY_ALIAS})

# 出借账号选择方式。manual = 发放时固定一把 Cursor Key；
# auto = 由 Auto Lender 选起始账号，之后代理在候选白名单内游走。
# 单一真相来源；DDL 默认值（models.py / migrate.py）按存储层惯例内联字面量。
LENDER_MODE_MANUAL = "manual"
LENDER_MODE_AUTO = "auto"
VALID_LENDER_MODES = frozenset({LENDER_MODE_MANUAL, LENDER_MODE_AUTO})


class KeyLoanError(ValueError):
    pass
