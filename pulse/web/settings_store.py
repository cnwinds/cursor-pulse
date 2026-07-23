from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from pulse.config import AppConfig
from pulse.settings.team_store import (
    EDITABLE_SECTIONS,
    effective_config,
    effective_config_dict,
    effective_config_for_tenant,
    load_team_settings_map,
    patch_team_setting,
)

REVEALABLE_SETTING_SECRETS: dict[str, frozenset[str]] = {
    "dingtalk": frozenset({"app_secret"}),
    "llm": frozenset({"api_key"}),
    "assistant_llm": frozenset({"api_key"}),
    "web_search": frozenset({"api_key"}),
    "integrations": frozenset({"webhook_secret"}),
}


def effective_chat_memory_dict(*, team_slug: str) -> dict[str, Any]:
    """Effective Assistant chat memory config (env + team overrides) for API display."""
    from assistant_platform.config import (
        AssistantConfig,
        _load_chat_memory_config,
        resolve_effective_chat_memory,
    )

    cfg = AssistantConfig(team_slug=team_slug, chat_memory=_load_chat_memory_config())
    return resolve_effective_chat_memory(cfg).model_dump()


def settings_for_api(base: AppConfig, session: Session, team_id: str) -> dict[str, Any]:
    from pulse.storage.models import Team

    data = effective_config_dict(base, session, team_id)
    team = session.get(Team, team_id)
    if team is not None:
        data["chat_memory"] = effective_chat_memory_dict(team_slug=team.slug)
    for section in ("llm", "assistant_llm", "web_search"):
        section_data = data.get(section, {})
        if section_data.get("api_key"):
            data[section] = {**section_data, "api_key": "***"}
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


def reveal_setting_secret(
    base: AppConfig,
    session: Session,
    *,
    team_id: str,
    section: str,
    key: str,
) -> str:
    allowed = REVEALABLE_SETTING_SECRETS.get(section)
    if not allowed or key not in allowed:
        raise ValueError(f"不可查看的配置项: {section}.{key}")
    value = str(effective_config_dict(base, session, team_id).get(section, {}).get(key) or "").strip()
    if not value:
        raise ValueError("该项尚未配置")
    return value


__all__ = [
    "EDITABLE_SECTIONS",
    "REVEALABLE_SETTING_SECRETS",
    "effective_chat_memory_dict",
    "effective_config",
    "effective_config_dict",
    "effective_config_for_tenant",
    "load_team_settings_map",
    "patch_team_setting",
    "reveal_setting_secret",
    "settings_for_api",
]
