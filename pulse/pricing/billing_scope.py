from __future__ import annotations

BillingScope = str  # auto_composer | api | external | excluded

_EXCLUDED_KIND_TOKENS = frozenset(
    {
        "ERRORED_NOT_CHARGED",
        "ERRORED_NO_CHARGE",
        "ABORTED_NOT_CHARGED",
        "ABORTED_NO_CHARGE",
    }
)
_FREE_KIND_TOKENS = frozenset({"FREE", "FREE_CREDIT"})
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


def normalize_event_kind(kind: str | None) -> str:
    """Canonicalize Cursor dashboard labels and USAGE_EVENT_KIND_* enums."""
    raw = (kind or "").strip()
    if not raw:
        return ""
    compact = "_".join(
        part for part in "".join(ch if ch.isalnum() else " " for ch in raw.upper()).split()
    )
    if compact.startswith("USAGE_EVENT_KIND_"):
        compact = compact[len("USAGE_EVENT_KIND_") :]
    return compact


def normalize_cursor_model_name(model: str | None) -> str:
    """Strip Cursor UI prefixes such as ``cursor-grok-4.5-high`` → ``grok-4.5-high``."""
    model_text = (model or "").lower().strip()
    if model_text.startswith("cursor-"):
        return model_text[len("cursor-") :]
    return model_text


def is_auto_composer_model(model: str | None) -> bool:
    """Auto Quota Pool heuristic (kind does not split Auto vs API; both are INCLUDED)."""
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


def kind_family(kind: str | None) -> str:
    """Coarse kind bucket for daily aggregates (INCLUDED_* variants collapse)."""
    token = normalize_event_kind(kind)
    if not token:
        return "unknown"
    if token == "USER_API_KEY":
        return "user_api_key"
    if token in _EXCLUDED_KIND_TOKENS or token in _FREE_KIND_TOKENS:
        return "excluded"
    if token.startswith("INCLUDED"):
        return "included"
    return "unknown"


KIND_FAMILY_LABELS = {
    "included": "套餐",
    "user_api_key": "BYOK",
    "excluded": "未计费",
    "unknown": "未知",
}


def pool_for_model(model: str | None) -> str:
    """Approximate analytics bucket from model name when kind_family is unknown.

    Not a Quota Pool. ``external`` is BYOK token volume.
    """
    if is_auto_composer_model(model):
        return "auto_composer"
    if is_likely_byok_model(model):
        return "external"
    return "api"


def pool_for_row(model: str | None, family: str | None = None) -> str:
    """Analytics bucket for a daily-agg row. Kind family wins over model heuristics."""
    family = (family or "").strip() or "unknown"
    if family == "user_api_key":
        return "external"
    if family == "excluded":
        return "excluded"
    if family == "included":
        if is_auto_composer_model(model):
            return "auto_composer"
        return "api"
    return pool_for_model(model)


def is_likely_byok_model(model: str | None) -> bool:
    """Model-only guess when kind is missing (daily agg / proxy).

    Cursor catalog slugs are lowercase-with-hyphens (``glm-5.2-high``).
    BYOK rows are often PascalCase (``GLM-5.2``, ``MiniMax-M2.7``).
    Lowercase BYOK such as ``deepseek-reasoner`` cannot be separated without kind.
    """
    text = (model or "").strip()
    if not text or not is_third_party_model(text):
        return False
    if text == text.lower() and "-" in text:
        return False
    return True


def classify_billing_scope(*, kind: str | None, model: str | None) -> BillingScope:
    """Classify a usage row for Cursor dual-pool billing.

    Kind is authoritative for BYOK vs included:
    ``USER_API_KEY`` → external (does not consume included API quota);
    ``INCLUDED_*`` named models including Cursor GLM → api.
    Auto vs API still uses model heuristics (both kinds are INCLUDED).
    """
    token = normalize_event_kind(kind)
    if token in _EXCLUDED_KIND_TOKENS:
        return "excluded"
    if token == "USER_API_KEY":
        return "external"
    if token in _FREE_KIND_TOKENS:
        return "excluded"

    # No kind on the row: PascalCase BYOK guess. INCLUDED + PascalCase GLM stays api.
    if not token and is_likely_byok_model(model):
        return "external"

    if is_auto_composer_model(model):
        return "auto_composer"
    return "api"


def is_cursor_billable_scope(scope: BillingScope) -> bool:
    return scope in ("auto_composer", "api")
