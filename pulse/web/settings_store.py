from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.storage.models import TeamSetting


EDITABLE_SECTIONS = frozenset(
    {"collection", "persona", "memory", "alerts", "llm", "integrations", "admin"}
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


def effective_config_dict(base: AppConfig, session: Session, team_id: str) -> dict[str, Any]:
    data = base.model_dump()
    overrides = load_team_settings_map(session, team_id)
    for section, section_data in overrides.items():
        if section in data and isinstance(section_data, dict):
            data[section] = _deep_merge(data[section], section_data)
    return data


def effective_config(base: AppConfig, session: Session, team_id: str) -> AppConfig:
    return AppConfig.model_validate(effective_config_dict(base, session, team_id))


def settings_for_api(base: AppConfig, session: Session, team_id: str) -> dict[str, Any]:
    data = effective_config_dict(base, session, team_id)
    llm = data.get("llm", {})
    if llm.get("api_key"):
        llm = {**llm, "api_key": "***"}
        data["llm"] = llm
    integrations = data.get("integrations", {})
    if integrations.get("webhook_secret"):
        integrations = {**integrations, "webhook_secret": "***"}
        data["integrations"] = integrations
    dingtalk = data.get("dingtalk", {})
    for key in ("app_secret",):
        if dingtalk.get(key):
            dingtalk = {**dingtalk, key: "***"}
    data["dingtalk"] = dingtalk
    return data


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
    return row.data
