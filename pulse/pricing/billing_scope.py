from __future__ import annotations

EXCLUDED_KINDS = frozenset(
    {
        "Errored, No Charge",
        "Aborted, Not Charged",
    }
)

EXTERNAL_KIND = "User API Key"

BillingScope = str  # auto_composer | api | external | excluded


def classify_billing_scope(*, kind: str | None, model: str | None) -> BillingScope:
    """Classify a usage row for Cursor dual-pool billing."""
    kind_text = (kind or "").strip()
    if kind_text == EXTERNAL_KIND:
        return "external"
    if kind_text in EXCLUDED_KINDS:
        return "excluded"

    model_text = (model or "").lower().strip()
    if model_text == "auto" or model_text.startswith("composer"):
        return "auto_composer"
    return "api"


def is_cursor_billable_scope(scope: BillingScope) -> bool:
    return scope in ("auto_composer", "api")
