from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from assistant_platform.llm.client import AssistantLlmClient


def test_complete_with_tools_sends_messages_and_parses_tool_calls():
    client = AssistantLlmClient(api_key="k", model="m", base_url="https://example.test/v1")
    payload_out = {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "quota_self_read",
                                "arguments": '{"period":"month"}',
                            },
                        }
                    ],
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = payload_out

    with patch("assistant_platform.llm.client.httpx.Client") as Client:
        Client.return_value.__enter__.return_value.post.return_value = mock_resp
        result = client.complete_with_tools(
            messages=[
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "查额度"},
            ],
            tools=[{"type": "function", "function": {"name": "quota_self_read", "parameters": {}}}],
        )

    assert result["content"] == ""
    assert result["tool_calls"][0]["id"] == "call_1"
    assert result["tool_calls"][0]["name"] == "quota_self_read"
    assert json.loads(result["tool_calls"][0]["arguments"]) == {"period": "month"}
    assert result["raw_assistant_message"]["tool_calls"][0]["id"] == "call_1"
    posted = Client.return_value.__enter__.return_value.post.call_args
    body = posted.kwargs["json"]
    assert body["messages"][0]["role"] == "system"
    assert body["tool_choice"] == "auto"
