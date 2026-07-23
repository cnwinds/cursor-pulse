from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatch


@dataclass(frozen=True)
class TokenRates:
    """Per-million-token USD rates."""

    input_no_cache: float
    input_cache_write: float
    cache_read: float
    output: float
    max_mode_multiplier: float = 1.0

    @classmethod
    def flat_input(cls, input_rate: float, output_rate: float, cache_read: float) -> TokenRates:
        return cls(
            input_no_cache=input_rate,
            input_cache_write=input_rate,
            cache_read=cache_read,
            output=output_rate,
        )


@dataclass(frozen=True)
class PricingRule:
    pattern: str
    match_type: str  # exact | glob | contains
    rates: TokenRates
    pool: str = "api"  # auto | api


@dataclass(frozen=True)
class PricingTable:
    vendor_slug: str
    version: str
    effective_from: date
    rules: tuple[PricingRule, ...]
    fallback: PricingRule | None = None

    def match_rule(self, model: str) -> PricingRule | None:
        name = (model or "").strip()
        if not name:
            return self.fallback
        for rule in self.rules:
            if _matches(rule, name):
                return rule
        return self.fallback


@dataclass(frozen=True)
class CostEstimate:
    cost_usd: float
    pricing_version: str
    pricing_rule: str
    confidence: float


def _matches(rule: PricingRule, model: str) -> bool:
    if rule.match_type == "exact":
        return model.lower() == rule.pattern.lower()
    if rule.match_type == "contains":
        return rule.pattern.lower() in model.lower()
    return fnmatch(model.lower(), rule.pattern.lower())


def estimate_token_cost(
    *,
    model: str,
    max_mode: bool,
    tokens_input_no_cache: int,
    tokens_input_cache_write: int,
    tokens_cache_read: int,
    tokens_output: int,
    table: PricingTable,
    pricing_rule_label: str | None = None,
    confidence: float | None = None,
) -> CostEstimate | None:
    rule = table.match_rule(model)
    if not rule:
        return None

    rates = rule.rates
    multiplier = rates.max_mode_multiplier
    if max_mode and multiplier == 1.0:
        multiplier = 1.0

    cost = (
        tokens_input_no_cache / 1_000_000 * rates.input_no_cache
        + tokens_input_cache_write / 1_000_000 * rates.input_cache_write
        + tokens_cache_read / 1_000_000 * rates.cache_read
        + tokens_output / 1_000_000 * rates.output
    ) * multiplier

    rule_label = pricing_rule_label or rule.pattern
    est_confidence = confidence
    if est_confidence is None:
        est_confidence = 0.95 if rule.match_type == "exact" else 0.8
        if rule is table.fallback:
            est_confidence = 0.6

    return CostEstimate(
        cost_usd=round(cost, 6),
        pricing_version=table.version,
        pricing_rule=rule_label,
        confidence=est_confidence,
    )
