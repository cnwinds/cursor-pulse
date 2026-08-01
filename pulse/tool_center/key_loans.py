"""Key Loan facade — re-exports lifecycle modules for existing callers.

Prefer concrete modules in new code:

- delivery / ``KeyLoanError`` → ``key_loan_delivery``
- store / ``KeyLoanService`` → ``key_loan_store``
- LoanState (revoke/expire) → ``key_loan_state``
- issuance / reassign → ``key_loan_issue``
- lender selection → ``key_loan_lender``
- presentation → ``key_loan_present``
- borrower helpers → ``key_loan_borrower``
"""

from __future__ import annotations

from pulse.integrations.cursor_api import CursorApiClient
from pulse.tool_center.key_loan_borrower import (
    borrower_has_bound_cursor_key,
    borrower_unbound_cursor_accounts,
    ensure_borrower_has_cursor_key,
)
from pulse.tool_center.key_loan_delivery import (
    DELIVERY_CURSOR_DIRECT,
    DELIVERY_PROXY_ALIAS,
    VALID_DELIVERY_MODES,
    KeyLoanError,
)
from pulse.tool_center.key_loan_issue import (
    finalize_reassign_old_remote_revoke,
    issue_loan_key,
    reassign_loan_source,
    request_self_service_loan,
)
from pulse.tool_center.key_loan_lender import (
    account_loan_deadline,
    build_lender_candidates,
    loan_display_expires_on,
    recommend_lender_for_borrower,
)
from pulse.tool_center.key_loan_present import (
    loan_payload,
    reveal_loan_cursor_key,
    reveal_loan_user_key,
)
from pulse.tool_center.key_loan_store import KeyLoanService

__all__ = [
    "CursorApiClient",
    "DELIVERY_CURSOR_DIRECT",
    "DELIVERY_PROXY_ALIAS",
    "VALID_DELIVERY_MODES",
    "KeyLoanError",
    "KeyLoanService",
    "account_loan_deadline",
    "borrower_has_bound_cursor_key",
    "borrower_unbound_cursor_accounts",
    "build_lender_candidates",
    "ensure_borrower_has_cursor_key",
    "finalize_reassign_old_remote_revoke",
    "issue_loan_key",
    "loan_display_expires_on",
    "loan_payload",
    "reassign_loan_source",
    "recommend_lender_for_borrower",
    "request_self_service_loan",
    "reveal_loan_cursor_key",
    "reveal_loan_user_key",
]
