from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig, ProxyAddress
from pulse.storage.models import TeamSetting

PROXY_ADDRESSES_REQUIRED_DETAIL = "尚未配置代理地址，请前往「系统设置 → 代理地址」添加"

EDITABLE_SECTIONS = frozenset(
    {
        "collection",
        "persona",
        "memory",
        "assistant_llm",
        "chat_memory",
        "web_search",
        "admin",
        "cursor_sync",
        "dingtalk",
        "feishu",
        "bot",
        "proxy_addresses",
    }
)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_team_settings_map(session: Session, team_id: str) -> dict[str, dict]:
    rows = session.scalars(select(TeamSetting).where(TeamSetting.team_id == team_id)).all()
    return {row.section: row.data for row in rows}


def configured_proxy_addresses(session: Session, team_id: str) -> list[ProxyAddress]:
    """Read team-setting proxy addresses without re-validating the full AppConfig."""
    overrides = load_team_settings_map(session, team_id)
    raw = overrides.get("proxy_addresses")
    if not isinstance(raw, dict):
        return []
    items = raw.get("addresses")
    if not isinstance(items, list):
        return []
    out: list[ProxyAddress] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        name = str(item.get("display_name") or "").strip()
        if not url:
            continue
        try:
            out.append(ProxyAddress(url=url, display_name=name or url))
        except Exception:
            continue
    return out


def effective_config_dict(base: AppConfig, session: Session, team_id: str) -> dict[str, Any]:
    data = base.model_dump()
    overrides = load_team_settings_map(session, team_id)
    for section, section_data in overrides.items():
        if section in data and isinstance(section_data, dict):
            data[section] = _deep_merge(data[section], section_data)
    return data


def effective_config(base: AppConfig, session: Session, team_id: str) -> AppConfig:
    return AppConfig.model_validate(effective_config_dict(base, session, team_id))


def effective_config_for_tenant(session: Session, base: AppConfig) -> AppConfig:
    from pulse.tenant.service import resolve_team

    team = resolve_team(session, base)
    return effective_config(base, session, team.id)


def patch_team_setting(
    session: Session,
    *,
    team_id: str,
    section: str,
    patch: dict,
    member_id: str | None,
) -> dict:
    if section not in EDITABLE_SECTIONS:
        raise ValueError(f"不可编辑的配置分区: {section}")

    row = session.scalar(
        select(TeamSetting).where(TeamSetting.team_id == team_id, TeamSetting.section == section)
    )
    now = datetime.now(timezone.utc)
    if row is None:
        row = TeamSetting(team_id=team_id, section=section, data=patch, updated_at=now)
        session.add(row)
    else:
        row.data = _deep_merge(row.data or {}, patch)
        row.updated_at = now
        row.updated_by_member_id = member_id
    session.flush()
    if section == "collection":
        from pulse.util.timezone_ctx import invalidate_display_timezone_cache

        invalidate_display_timezone_cache()
    return row.data
