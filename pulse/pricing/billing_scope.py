from __future__ import annotations

EXCLUDED_KINDS = frozenset(
    {
        "Errored, No Charge",
        "Aborted, Not Charged",
    }
)

EXTERNAL_KIND = "User API Key"

BillingScope = str  # auto_composer | api | third_party | external | excluded

_THIRD_PARTY_MARKERS = (
    "glm",
    "minimax",
    "deepseek",
    "qwen",
    "kimi",
    "moonshot",
    "doubao",
    "baichuan",
)


def normalize_cursor_model_name(model: str | None) -> str:
    """Strip Cursor UI prefixes such as ``cursor-grok-4.5-high`` → ``grok-4.5-high``."""
    model_text = (model or "").lower().strip()
    if model_text.startswith("cursor-"):
        return model_text[len("cursor-") :]
    return model_text


def is_auto_composer_model(model: str | None) -> bool:
    normalized = normalize_cursor_model_name(model)
    return (
        normalized in {"auto", "default"}
        or normalized.startswith("composer")
        or normalized.startswith("grok")
    )


def is_third_party_model(model: str | None) -> bool:
    model_text = (model or "").lower().strip()
    if not model_text:
        return False
    return any(marker in model_text for marker in _THIRD_PARTY_MARKERS)


def classify_billing_scope(*, kind: str | None, model: str | None) -> BillingScope:
    """Classify a usage row for Cursor dual-pool billing."""
    kind_text = (kind or "").strip()
    if kind_text == EXTERNAL_KIND:
        return "external"
    if kind_text in EXCLUDED_KINDS:
        return "excluded"

    model_text = (model or "").lower().strip()
    if is_auto_composer_model(model):
        return "auto_composer"
    if is_third_party_model(model):
        return "third_party"
    return "api"


def is_cursor_billable_scope(scope: BillingScope) -> bool:
    return scope in ("auto_composer", "api", "third_party")
