from __future__ import annotations

from assistant_platform.config import AssistantConfig, resolve_effective_llm
from assistant_platform.llm.client import AssistantLlmClient


def build_assistant_llm_client(config: AssistantConfig) -> AssistantLlmClient | None:
    llm = resolve_effective_llm(config)
    if not llm.enabled or not llm.api_key or not llm.model:
        return None
    return AssistantLlmClient(
        api_key=llm.api_key,
        model=llm.model,
        base_url=llm.base_url,
        timeout_seconds=llm.timeout_seconds,
    )
