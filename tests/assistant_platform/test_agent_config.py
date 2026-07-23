from assistant_platform.config import AssistantConfig, AssistantLlmConfig


def test_agent_defaults_on_llm_config():
    llm = AssistantLlmConfig()
    assert llm.agent_max_tool_rounds == 20
    assert llm.agent_history_max_messages == 40
    assert llm.agent_total_timeout_seconds == 120.0


def test_assistant_config_embeds_agent_defaults():
    cfg = AssistantConfig()
    assert cfg.llm.agent_max_tool_rounds == 20
