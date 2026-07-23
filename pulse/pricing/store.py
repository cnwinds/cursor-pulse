from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.pricing.cursor_tables import builtin_cursor_pricing_table, pricing_table_to_dict
from pulse.pricing.types import PricingRule, PricingTable, TokenRates
from pulse.storage.models import TeamSetting

CURSOR_PRICING_SECTION = "cursor_pricing"
_MATCH_TYPES = frozenset({"exact", "glob", "contains"})
_POOLS = frozenset({"auto", "api"})


def _rate(value: Any, field: str) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"费率 {field} 必须是数字") from e
    if n < 0:
        raise ValueError(f"费率 {field} 不能为负")
    return n


def _parse_rates(raw: Any) -> TokenRates:
    if not isinstance(raw, dict):
        raise ValueError("rates 必须是对象")
    return TokenRates(
        input_no_cache=_rate(raw.get("input_no_cache"), "input_no_cache"),
        input_cache_write=_rate(raw.get("input_cache_write"), "input_cache_write"),
        cache_read=_rate(raw.get("cache_read"), "cache_read"),
        output=_rate(raw.get("output"), "output"),
        max_mode_multiplier=_rate(raw.get("max_mode_multiplier", 1.0), "max_mode_multiplier"),
    )


def _parse_rule(raw: Any, *, required: bool = True) -> PricingRule | None:
    if raw is None:
        if required:
            raise ValueError("缺少规则")
        return None
    if not isinstance(raw, dict):
        raise ValueError("规则必须是对象")
    pattern = str(raw.get("pattern") or "").strip()
    if not pattern:
        raise ValueError("匹配模式不能为空")
    match_type = str(raw.get("match_type") or "glob").strip().lower()
    if match_type not in _MATCH_TYPES:
        raise ValueError(f"match_type 必须是 {sorted(_MATCH_TYPES)}")
    pool = str(raw.get("pool") or "api").strip().lower()
    if pool not in _POOLS:
        raise ValueError(f"pool 必须是 {sorted(_POOLS)}")
    return PricingRule(
        pattern=pattern,
        match_type=match_type,
        rates=_parse_rates(raw.get("rates")),
        pool=pool,
    )


def validate_pricing_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a pricing table payload; returns JSON-ready dict."""
    if not isinstance(data, dict):
        raise ValueError("定价表必须是对象")
    version = str(data.get("version") or "").strip() or "cursor-custom"
    vendor_slug = str(data.get("vendor_slug") or "cursor").strip() or "cursor"
    rules_raw = data.get("rules")
    if rules_raw is None:
        rules_raw = []
    if not isinstance(rules_raw, list):
        raise ValueError("rules 必须是数组")
    rules = [_parse_rule(item) for item in rules_raw]
    fallback = _parse_rule(data.get("fallback"), required=True)
    assert fallback is not None
    if not rules and fallback is None:
        raise ValueError("至少需要一条规则或 fallback")
    effective_from = data.get("effective_from")
    if effective_from:
        try:
            date.fromisoformat(str(effective_from)[:10])
        except ValueError as e:
            raise ValueError("effective_from 须为 YYYY-MM-DD") from e
    else:
        effective_from = date.today().isoformat()

    table = PricingTable(
        vendor_slug=vendor_slug,
        version=version,
        effective_from=date.fromisoformat(str(effective_from)[:10]),
        rules=tuple(rules),
        fallback=fallback,
    )
    return pricing_table_to_dict(table)


def pricing_table_from_dict(data: dict[str, Any]) -> PricingTable:
    normalized = validate_pricing_payload(data)
    rules = tuple(_parse_rule(r) for r in normalized["rules"])
    fallback = _parse_rule(normalized["fallback"])
    return PricingTable(
        vendor_slug=normalized["vendor_slug"],
        version=normalized["version"],
        effective_from=date.fromisoformat(normalized["effective_from"]),
        rules=rules,
        fallback=fallback,
    )


def get_team_pricing_row(session: Session, team_id: str) -> TeamSetting | None:
    return session.scalar(
        select(TeamSetting).where(
            TeamSetting.team_id == team_id,
            TeamSetting.section == CURSOR_PRICING_SECTION,
        )
    )


def load_team_cursor_pricing(
    session: Session, team_id: str
) -> tuple[PricingTable, str, TeamSetting | None]:
    """Return (table, source, row). source is override|builtin."""
    row = get_team_pricing_row(session, team_id)
    if row and isinstance(row.data, dict) and row.data:
        try:
            return pricing_table_from_dict(row.data), "override", row
        except ValueError:
            pass
    return builtin_cursor_pricing_table(), "builtin", row


def save_team_cursor_pricing(
    session: Session,
    *,
    team_id: str,
    data: dict[str, Any],
    member_id: str | None,
) -> TeamSetting:
    normalized = validate_pricing_payload(data)
    now = datetime.now(timezone.utc)
    row = get_team_pricing_row(session, team_id)
    if row is None:
        row = TeamSetting(
            team_id=team_id,
            section=CURSOR_PRICING_SECTION,
            data=normalized,
            updated_at=now,
            updated_by_member_id=member_id,
        )
        session.add(row)
    else:
        row.data = normalized
        row.updated_at = now
        row.updated_by_member_id = member_id
    session.flush()
    return row


def reset_team_cursor_pricing(session: Session, team_id: str) -> bool:
    """Delete team override. Returns True if a row was removed."""
    row = get_team_pricing_row(session, team_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def pricing_api_payload(
    session: Session, team_id: str, *, member_names: dict[str, str] | None = None
) -> dict[str, Any]:
    table, source, row = load_team_cursor_pricing(session, team_id)
    payload = pricing_table_to_dict(table)
    payload["source"] = source
    payload["updated_at"] = row.updated_at.isoformat() if row and row.updated_at else None
    updated_by = None
    if row and row.updated_by_member_id:
        names = member_names or {}
        updated_by = {
            "member_id": row.updated_by_member_id,
            "display_name": names.get(row.updated_by_member_id),
        }
    payload["updated_by"] = updated_by
    payload["builtin"] = pricing_table_to_dict(builtin_cursor_pricing_table())
    return payload
