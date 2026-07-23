from __future__ import annotations

from unittest.mock import patch

from assistant_platform.config import AssistantConfig, AssistantLlmConfig
from assistant_platform.llm import build_assistant_llm_client


def _effective_llm(config: AssistantConfig) -> AssistantLlmConfig:
    return config.llm


def test_llm_disabled_returns_none():
    cfg = AssistantConfig(team_id="t1", llm=AssistantLlmConfig(enabled=False))
    with patch("assistant_platform.llm.resolve_effective_llm", side_effect=_effective_llm):
        assert build_assistant_llm_client(cfg) is None


def test_llm_enabled_without_key_returns_none():
    cfg = AssistantConfig(
        team_id="t1",
        llm=AssistantLlmConfig(enabled=True, api_key="", model="gpt-4o-mini"),
    )
    with patch("assistant_platform.llm.resolve_effective_llm", side_effect=_effective_llm):
        assert build_assistant_llm_client(cfg) is None


def test_llm_enabled_with_key_returns_client():
    cfg = AssistantConfig(
        team_id="t1",
        llm=AssistantLlmConfig(
            enabled=True,
            api_key="sk-test",
            base_url="https://api.example.com/v1",
            model="gpt-4o-mini",
        ),
    )
    with patch("assistant_platform.llm.resolve_effective_llm", side_effect=_effective_llm):
        client = build_assistant_llm_client(cfg)
    assert client is not None
    assert client.model == "gpt-4o-mini"
