from __future__ import annotations


DELIVERY_CURSOR_DIRECT = "cursor_direct"
DELIVERY_PROXY_ALIAS = "proxy_alias"
VALID_DELIVERY_MODES = frozenset({DELIVERY_CURSOR_DIRECT, DELIVERY_PROXY_ALIAS})


class KeyLoanError(ValueError):
    pass
