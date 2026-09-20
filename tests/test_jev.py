from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from pulse.config import AppConfig, JevConfig, ToolCenterConfig, load_config
from pulse.llm.jev import JevClient, JevError, build_jev_client


def test_jev_client_posts_system_one():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={"answers": {"waste:a": {"type": "noul", "noul": 0.7}}},
        )

    client = JevClient(api_key="sk-test", base_url="https://api.typesafe.ai", timeout_seconds=2)
    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch("pulse.llm.jev.outbound_client", return_value=transport_client):
        data = client.system_one(
            state={"goal": "rank"},
            questions={"waste:a": {"type": "noul"}},
        )
    assert captured["url"].endswith("/v1/systemone")
    assert captured["auth"] == "Bearer sk-test"
    assert captured["body"]["state"] == {"goal": "rank"}
    assert data["answers"]["waste:a"]["noul"] == 0.7


def test_jev_client_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope")

    client = JevClient(api_key="sk-test", timeout_seconds=2)
    transport_client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch("pulse.llm.jev.outbound_client", return_value=transport_client):
        with pytest.raises(JevError):
            client.system_one(state={}, questions={"q": {"type": "noul"}})


def test_build_jev_client_requires_enabled_key():
    cfg = AppConfig(tool_center=ToolCenterConfig(jev=JevConfig(enabled=True, api_key="")))
    assert build_jev_client(cfg) is None
    cfg.tool_center.jev.api_key = "sk"
    assert build_jev_client(cfg) is not None
    cfg.tool_center.jev.enabled = False
    assert build_jev_client(cfg) is None


def test_typesafe_api_key_enables_jev(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-jev")
    monkeypatch.delenv("TYPESAFE_ENABLED", raising=False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("tenant:\n  slug: test\n", encoding="utf-8")
    cfg = load_config(cfg_path)
    assert cfg.tool_center.jev.api_key == "sk-jev"
    assert cfg.tool_center.jev.enabled is True
