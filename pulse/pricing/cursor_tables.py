from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from pulse.pricing.types import PricingRule, PricingTable, TokenRates

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# Cursor docs: https://cursor.com/docs/models-and-pricing
# Auto Cost — flat rates regardless of routed model
_AUTO = TokenRates.flat_input(1.25, 6.0, 0.25)
# Composer 2.5 standard
_COMPOSER_25 = TokenRates(
    input_no_cache=0.5,
    input_cache_write=0.5,
    cache_read=0.2,
    output=2.5,
)
# Composer 2.5 fast variant
_COMPOSER_25_FAST = TokenRates(
    input_no_cache=3.0,
    input_cache_write=3.0,
    cache_read=0.3,
    output=15.0,
)
_CLAUDE_SONNET = TokenRates(
    input_no_cache=3.0,
    input_cache_write=3.75,
    cache_read=0.3,
    output=15.0,
)
_CLAUDE_OPUS = TokenRates(
    input_no_cache=5.0,
    input_cache_write=6.25,
    cache_read=0.5,
    output=25.0,
    max_mode_multiplier=1.0,
)
_GLM = TokenRates.flat_input(0.5, 2.0, 0.1)
_GPT_CODEX = TokenRates(
    input_no_cache=2.0,
    input_cache_write=2.5,
    cache_read=0.2,
    output=10.0,
)
# GPT-5.6 Sol（官方 charged 近似档；须排在 gpt-* Codex 之前）
_GPT_SOL = TokenRates(
    input_no_cache=5.0,
    input_cache_write=6.25,
    cache_read=0.5,
    output=30.0,
)

CURSOR_PRICING_V2026_07 = PricingTable(
    vendor_slug="cursor",
    version="cursor-2026-07",
    effective_from=date(2026, 1, 1),
    rules=(
        PricingRule("auto", "exact", _AUTO, pool="auto"),
        PricingRule("composer-*-fast", "glob", _COMPOSER_25_FAST, pool="auto"),
        PricingRule("composer-*", "glob", _COMPOSER_25, pool="auto"),
        PricingRule("premium", "exact", _CLAUDE_SONNET, pool="api"),
        PricingRule("Premium (*)", "glob", _GPT_CODEX, pool="api"),
        PricingRule("Premium (Codex 5.3)", "exact", _GPT_CODEX, pool="api"),
        PricingRule("glm-*", "glob", _GLM, pool="api"),
        PricingRule("GLM-*", "glob", _GLM, pool="api"),
        PricingRule("*opus*", "glob", _CLAUDE_OPUS, pool="api"),
        PricingRule("claude-*", "glob", _CLAUDE_SONNET, pool="api"),
        PricingRule("gpt-5.6-sol*", "glob", _GPT_SOL, pool="api"),
        PricingRule("gpt-*", "glob", _GPT_CODEX, pool="api"),
        PricingRule("codex-*", "glob", _GPT_CODEX, pool="api"),
        PricingRule("o3*", "glob", _GPT_CODEX, pool="api"),
        PricingRule("o1*", "glob", _GPT_CODEX, pool="api"),
    ),
    fallback=PricingRule("auto", "exact", _AUTO, pool="auto"),
)

# Backward-compatible alias for existing imports/tests
CURSOR_PRICING_V2026_06 = CURSOR_PRICING_V2026_07


def builtin_cursor_pricing_table() -> PricingTable:
    return CURSOR_PRICING_V2026_07


def _rule_to_dict(rule: PricingRule) -> dict:
    return {
        "pattern": rule.pattern,
        "match_type": rule.match_type,
        "pool": rule.pool,
        "rates": {
            "input_no_cache": rule.rates.input_no_cache,
            "input_cache_write": rule.rates.input_cache_write,
            "cache_read": rule.rates.cache_read,
            "output": rule.rates.output,
            "max_mode_multiplier": rule.rates.max_mode_multiplier,
        },
    }


def pricing_table_to_dict(table: PricingTable) -> dict:
    return {
        "vendor_slug": table.vendor_slug,
        "version": table.version,
        "effective_from": table.effective_from.isoformat(),
        "rules": [_rule_to_dict(r) for r in table.rules],
        "fallback": _rule_to_dict(table.fallback) if table.fallback else None,
    }


def get_cursor_pricing_table(
    _event_date: date | None = None,
    *,
    session: Session | None = None,
    team_id: str | None = None,
) -> PricingTable:
    if session is not None and team_id:
        from pulse.pricing.store import load_team_cursor_pricing

        table, _source, _row = load_team_cursor_pricing(session, team_id)
        return table
    return builtin_cursor_pricing_table()
