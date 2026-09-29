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

    with patch("assistant_platform.llm.client.outbound_client") as Client:
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


def _sse(chunks: list[dict]) -> list[str]:
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps(chunk, ensure_ascii=False))
        lines.append("")
    lines.append("data: [DONE]")
    return lines


def _stream_client(lines: list[str]):
    Client = patch("assistant_platform.llm.client.outbound_client")
    mock_cls = Client.start()
    stream_resp = MagicMock()
    stream_resp.raise_for_status = MagicMock()
    stream_resp.iter_lines.return_value = iter(lines)
    ctx = MagicMock()
    ctx.__enter__.return_value = stream_resp
    mock_cls.return_value.__enter__.return_value.stream.return_value = ctx
    return Client, mock_cls


def test_complete_with_tools_streams_content_deltas():
    client = AssistantLlmClient(api_key="k", model="m", base_url="https://example.test/v1")
    lines = _sse(
        [
            {"choices": [{"delta": {"role": "assistant", "content": ""}}]},
            {"choices": [{"delta": {"content": "### 你的"}}]},
            {"choices": [{"delta": {"content": "用量"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
    )
    patcher, mock_cls = _stream_client(lines)
    seen: list[str] = []
    try:
        result = client.complete_with_tools(
            messages=[{"role": "user", "content": "我的用量"}],
            tools=[],
            on_content_delta=seen.append,
        )
    finally:
        patcher.stop()

    assert seen == ["### 你的", "### 你的用量"]
    assert result["content"] == "### 你的用量"
    assert result["tool_calls"] == []
    assert result["raw_assistant_message"] == {"role": "assistant", "content": "### 你的用量"}
    method, url = mock_cls.return_value.__enter__.return_value.stream.call_args.args
    assert method == "POST"
    body = mock_cls.return_value.__enter__.return_value.stream.call_args.kwargs["json"]
    assert body["stream"] is True


def test_complete_with_tools_stream_assembles_tool_calls_and_reasoning():
    client = AssistantLlmClient(api_key="k", model="m", base_url="https://example.test/v1")
    lines = _sse(
        [
            {"choices": [{"delta": {"reasoning_content": "想一下"}}]},
            {"choices": [{"delta": {"content": "好的，我来查"}}]},
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "usage_self_read", "arguments": '{"per'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'iod":"month"}'}}]}}
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
    )
    patcher, _ = _stream_client(lines)
    try:
        result = client.complete_with_tools(
            messages=[{"role": "user", "content": "我的用量"}],
            tools=[],
            on_content_delta=lambda _t: None,
        )
    finally:
        patcher.stop()

    assert result["content"] == "好的，我来查"
    assert result["reasoning"] == "想一下"
    assert result["tool_calls"] == [
        {"id": "call_1", "name": "usage_self_read", "arguments": '{"period":"month"}'}
    ]
    raw = result["raw_assistant_message"]
    assert raw["reasoning_content"] == "想一下"
    assert raw["tool_calls"][0]["function"]["arguments"] == '{"period":"month"}'
