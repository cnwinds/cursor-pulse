from __future__ import annotations

import json

import httpx
import pytest

from pulse.channels.feishu.messenger import FEISHU_OPEN_API, FeishuMessenger
from pulse.config import AppConfig, FeishuConfig


def _config() -> AppConfig:
    return AppConfig(feishu=FeishuConfig(app_id="cli_test", app_secret="secret-1"))


def _messenger(handler) -> FeishuMessenger:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FeishuMessenger(_config(), http_client=client)


def test_get_access_token_fetches_and_caches():
    calls = {"token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/auth/v3/tenant_access_token/internal")
        body = json.loads(request.content)
        assert body == {"app_id": "cli_test", "app_secret": "secret-1"}
        calls["token"] += 1
        return httpx.Response(
            200,
            json={"code": 0, "msg": "ok", "tenant_access_token": "t-abc", "expire": 7200},
        )

    messenger = _messenger(handler)
    token1 = messenger.get_access_token()
    token2 = messenger.get_access_token()
    assert token1 == "t-abc"
    assert token2 == "t-abc"
    # Second call must hit the cache, not the network.
    assert calls["token"] == 1


def test_get_access_token_raises_on_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 99991663, "msg": "app secret invalid"})

    messenger = _messenger(handler)
    with pytest.raises(RuntimeError, match="app secret invalid"):
        messenger.get_access_token()


def test_send_oto_text_posts_message_with_open_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-xyz", "expire": 7200}
            )
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})

    messenger = _messenger(handler)
    result = messenger.send_oto_text("ou_user_open_id", "hello there")

    assert result["code"] == 0
    assert "receive_id_type=open_id" in seen["url"]
    assert seen["headers"]["authorization"] == "Bearer t-xyz"
    assert seen["body"]["receive_id"] == "ou_user_open_id"
    assert seen["body"]["msg_type"] == "text"
    assert json.loads(seen["body"]["content"]) == {"text": "hello there"}


def test_send_group_text_uses_configured_chat_id():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-xyz", "expire": 7200}
            )
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})

    config = _config()
    config.feishu.group_chat_id = "oc_group_chat"
    client = httpx.Client(transport=httpx.MockTransport(handler))
    messenger = FeishuMessenger(config, http_client=client)

    messenger.send_group_text("group announcement")

    assert "receive_id_type=chat_id" in seen["url"]
    assert seen["body"]["receive_id"] == "oc_group_chat"
    assert json.loads(seen["body"]["content"]) == {"text": "group announcement"}


def test_send_group_text_requires_configured_chat_id():
    messenger = _messenger(lambda request: httpx.Response(200, json={"code": 0}))
    with pytest.raises(RuntimeError):
        messenger.send_group_text("no chat id configured")


def test_send_message_raises_on_error_code():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-xyz", "expire": 7200}
            )
        return httpx.Response(200, json={"code": 12345, "msg": "receiver not found"})

    messenger = _messenger(handler)
    with pytest.raises(RuntimeError, match="receiver not found"):
        messenger.send_oto_text("missing_open_id", "hi")


def test_send_text_to_chat_uses_chat_id_receive_type():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t-xyz", "expire": 7200}
            )
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})

    messenger = _messenger(handler)
    messenger.send_text_to_chat("oc_any_chat", "reply text")

    assert "receive_id_type=chat_id" in seen["url"]
    assert seen["body"]["receive_id"] == "oc_any_chat"


def test_module_uses_open_feishu_api_base():
    assert FEISHU_OPEN_API == "https://open.feishu.cn/open-apis"
