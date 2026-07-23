from __future__ import annotations

from typing import Any, Iterable

from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.memory.agent_tools import is_local_memory_tool, memory_tool_definitions
from assistant_platform.skills.agent_tools import is_local_skill_tool, load_skill_docs_tool_definition

TOOL_EXCLUSIONS = frozenset({"bot.help"})
NOTIFY_USER_TOOL_NAME = "notify_user"

# 成功路径一律结构化 result + LLM 排版，不再 verbatim 直出 user_message。
VERBATIM_PRIVATE_CAPABILITIES: frozenset[str] = frozenset()


def tool_name_for_capability(capability_key: str) -> str:
    return capability_key.replace(".", "_")


def notify_user_tool_definition() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": NOTIFY_USER_TOOL_NAME,
            "description": (
                "向用户发送进度或安抚消息，不结束当前任务。"
                "收到新任务后、多步 tool 执行之间、或用户催问进度（如「能查吗」「怎么样了」）时主动使用。"
                "话术简短具体，说明当前在做什么。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "发给用户的简短进度或安抚话术",
                    }
                },
                "required": ["message"],
                "additionalProperties": False,
            },
        },
    }


def is_builtin_tool(tool_name: str) -> bool:
    return tool_name == NOTIFY_USER_TOOL_NAME


def tools_from_capabilities(
    capabilities: Iterable[ResolvedCapability],
    *,
    include_memory_tools: bool = False,
    include_skill_tools: bool = False,
) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for cap in capabilities:
        if cap.key in TOOL_EXCLUSIONS:
            continue
        schema = cap.input_schema or {"type": "object", "properties": {}}
        description = f"{cap.display_name}。{cap.description}".strip()
        if cap.risk_level in ("sensitive", "destructive") or cap.confirmation_required:
            description += (
                " 【高风险】调用前必须先向用户说明将执行的操作并获得明确同意。"
            )
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_name_for_capability(cap.key),
                    "description": description,
                    "parameters": schema,
                },
            }
        )
    if include_memory_tools:
        tools.extend(memory_tool_definitions())
    if include_skill_tools:
        tools.append(load_skill_docs_tool_definition())
    tools.append(notify_user_tool_definition())
    return tools


def resolve_capability_for_tool_name(
    tool_name: str,
    capabilities: Iterable[ResolvedCapability],
) -> ResolvedCapability | None:
    by_name = {tool_name_for_capability(c.key): c for c in capabilities}
    return by_name.get(tool_name)
