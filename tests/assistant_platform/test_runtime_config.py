from __future__ import annotations

import pytest

from assistant_platform.config import (
    AssistantChatMemoryConfig,
    AssistantConfig,
    MemoryArchiveConfig,
    MemoryFeatureFlags,
    _apply_team_chat_memory_overrides,
)


def test_apply_team_settings_overrides_false_skips_db_merge():
    cfg = AssistantConfig(
        team_slug="default",
        apply_team_settings_overrides=False,
        chat_memory=AssistantChatMemoryConfig(
            archive=MemoryArchiveConfig(enabled=True),
            features=MemoryFeatureFlags(distill_on_close=False),
        ),
    )
    resolved = _apply_team_chat_memory_overrides(cfg)
    assert resolved.features.distill_on_close is False


def test_validate_runtime_config_requires_service_token():
    from assistant_platform.config import validate_runtime_config

    with pytest.raises(SystemExit, match="ASSISTANT_SERVICE_TOKEN"):
        validate_runtime_config(AssistantConfig(service_token=""))
