from __future__ import annotations

from datetime import date

from pulse.pricing.types import PricingRule, PricingTable, TokenRates

# Cursor docs (2026): Auto pool — input/cache-write $1.25, output $6, cache read $0.25 per 1M
_AUTO = TokenRates.flat_input(1.25, 6.0, 0.25)
_COMPOSER_25 = TokenRates(
    input_no_cache=1.5,
    input_cache_write=1.5,
    cache_read=0.2,
    output=2.5,
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

CURSOR_PRICING_V2026_06 = PricingTable(
    vendor_slug="cursor",
    version="cursor-2026-06",
    effective_from=date(2026, 1, 1),
    rules=(
        PricingRule("auto", "exact", _AUTO, pool="auto"),
        PricingRule("composer-*", "glob", _COMPOSER_25, pool="auto"),
        PricingRule("premium", "exact", _CLAUDE_SONNET, pool="api"),
        PricingRule("Premium (*)", "glob", _GPT_CODEX, pool="api"),
        PricingRule("Premium (Codex 5.3)", "exact", _GPT_CODEX, pool="api"),
        PricingRule("glm-*", "glob", _GLM, pool="api"),
        PricingRule("GLM-*", "glob", _GLM, pool="api"),
        PricingRule("claude-*", "glob", _CLAUDE_SONNET, pool="api"),
        PricingRule("gpt-*", "glob", _GPT_CODEX, pool="api"),
        PricingRule("codex-*", "glob", _GPT_CODEX, pool="api"),
        PricingRule("o3*", "glob", _GPT_CODEX, pool="api"),
        PricingRule("o1*", "glob", _GPT_CODEX, pool="api"),
    ),
    fallback=PricingRule("auto", "exact", _AUTO, pool="auto"),
)


def get_cursor_pricing_table(_event_date: date | None = None) -> PricingTable:
    return CURSOR_PRICING_V2026_06
