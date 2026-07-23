# tests/assistant_platform/test_agent_tools.py
from assistant_platform.capabilities.resolve import ResolvedCapability
from assistant_platform.conversation.agent_tools import (
    NOTIFY_USER_TOOL_NAME,
    TOOL_EXCLUSIONS,
    tools_from_capabilities,
    tool_name_for_capability,
    resolve_capability_for_tool_name,
)


def test_tool_name_roundtrip_via_resolve():
    cap = ResolvedCapability(
        key="quota.self.read",
        version="1",
        risk_level="read",
        display_name="查询本人额度",
        description="读取额度快照",
        input_schema={"type": "object", "properties": {"period": {"type": "string"}}},
    )
    assert tool_name_for_capability(cap.key) == "quota_self_read"
    assert resolve_capability_for_tool_name("quota_self_read", [cap]) is cap


def test_bot_help_excluded():
    caps = [
        ResolvedCapability(
            key="bot.help",
            version="1",
            risk_level="read",
            display_name="帮助",
            description="帮助",
            input_schema={"type": "object", "properties": {}},
        ),
        ResolvedCapability(
            key="quota.self.read",
            version="1",
            risk_level="read",
            display_name="查询本人额度",
            description="读取额度快照",
            input_schema={
                "type": "object",
                "properties": {"period": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
    ]
    tools = tools_from_capabilities(caps)
    names = [t["function"]["name"] for t in tools]
    assert "bot_help" not in names
    assert "quota_self_read" in names
    assert NOTIFY_USER_TOOL_NAME in names
    assert "bot.help" in TOOL_EXCLUSIONS
    quota = next(t for t in tools if t["function"]["name"] == "quota_self_read")
    assert quota["type"] == "function"
    assert "额度" in quota["function"]["description"]
    assert quota["function"]["parameters"]["properties"]["period"]["type"] == "string"
