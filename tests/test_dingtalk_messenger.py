from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pulse.channels.dingtalk.files import (
    extract_file_attachment,
    extract_incoming_text,
    extract_picture_download_code,
    incoming_message_type,
    normalize_incoming_text,
)
from pulse.channels.dingtalk.handler import DingTalkChannelHandler
from pulse.channels.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig, AssistantMirrorConfig, DingTalkConfig, TenantConfig
from pulse.storage.db import init_db
from tests.conftest import make_team_repo


def test_normalize_incoming_text_strips_at_bot():
    assert normalize_incoming_text("@CursorBot 状态") == "状态"
    assert normalize_incoming_text("/status") == "/status"


def test_extract_file_attachment():
    raw = {
        "msgtype": "file",
        "content": {
            "fileName": "usage-events.csv",
            "downloadCode": "abc123",
        },
    }
    assert extract_file_attachment(raw) == ("usage-events.csv", "abc123")


def test_extract_file_attachment_missing_code():
    assert extract_file_attachment({"msgtype": "file", "content": {}}) is None


def test_extract_file_attachment_msgtype_camel_case():
    raw = {
        "msgType": "file",
        "content": {
            "fileName": "usage-events.xlsx",
            "downloadCode": "abc123",
        },
    }
    assert extract_file_attachment(raw) == ("usage-events.xlsx", "abc123")


def test_extract_file_attachment_content_json_string():
    raw = {
        "msgtype": "file",
        "content": '{"fileName":"usage-events.xlsx","downloadCode":"abc123"}',
    }
    assert extract_file_attachment(raw) == ("usage-events.xlsx", "abc123")


def test_extract_file_attachment_from_extensions():
    class Incoming:
        message_type = "file"
        extensions = {
            "content": {
                "fileName": "usage-events.csv",
                "downloadCode": "ext123",
            }
        }

    assert extract_file_attachment({}, Incoming()) == ("usage-events.csv", "ext123")


def test_incoming_message_type_prefers_raw():
    assert incoming_message_type({"msgtype": "file"}) == "file"
    assert incoming_message_type({"msgType": "File"}) == "file"


def test_extract_picture_download_code_from_richtext_raw():
    raw = {
        "msgtype": "richText",
        "content": {
            "richText": [
                {"downloadCode": "img123"},
                {"text": "提交智谱的用量"},
            ]
        },
    }
    assert extract_picture_download_code(raw) == "img123"


def test_extract_incoming_text_from_richtext():
    class RichTextItem:
        def __init__(self, data):
            self._data = data

    class RichTextContent:
        rich_text_list = [{"text": "提交智谱的用量"}]

        def to_dict(self):
            return {"richText": self.rich_text_list}

    class Incoming:
        message_type = "richText"
        text = None
        rich_text_content = RichTextContent()

        def get_text_list(self):
            return ["提交智谱的用量"]

    assert extract_incoming_text(Incoming()) == "提交智谱的用量"


@pytest.fixture
def bot_env():
    config = AppConfig(
        tenant=TenantConfig(slug="test", name="Test"),
        assistant_mirror=AssistantMirrorConfig(
            enabled=True,
            base_url="http://assistant.test",
            service_token="tok",
        ),
    )
    messenger = MagicMock()
    session = init_db("sqlite:///:memory:")()
    team, repo = make_team_repo(session)
    repo.get_or_create_member("u1", "Alice")
    session.commit()

    handler = DingTalkChannelHandler(
        config=config,
        session_factory=lambda: session,
        messenger=messenger,
    )
    return {"handler": handler, "session": session, "team": team, "messenger": messenger}


def test_picture_with_text_skips_assistant_mirror(bot_env):
    handler = bot_env["handler"]
    incoming = MagicMock()
    incoming.conversation_type = "1"
    incoming.is_in_at_list = True
    incoming.sender_staff_id = "u1"
    incoming.sender_id = "u1"
    incoming.sender_nick = "Alice"
    incoming.conversation_id = "u1"
    incoming.message_id = "msg-pic"
    incoming.text = None
    incoming.message_type = "richText"
    incoming.image_content = None

    def get_text_list():
        return ["提交智谱的用量"]

    def get_image_list():
        return ["img-code-1"]

    incoming.get_text_list = get_text_list
    incoming.get_image_list = get_image_list

    raw = {
        "msgtype": "richText",
        "content": {
            "richText": [
                {"downloadCode": "img-code-1"},
                {"text": "提交智谱的用量"},
            ]
        },
    }

    with (
        patch("pulse.channels.dingtalk.mirror.mirror_dingtalk_message") as mirror,
        patch.object(handler, "_handle_picture", new=AsyncMock()) as handle_picture,
    ):
        async def run():
            await handler._handle_message(incoming, raw)

        asyncio.run(run())
        mirror.assert_not_called()
        handle_picture.assert_called_once()
        assert handle_picture.call_args.kwargs.get("text_hint") == "提交智谱的用量"


@pytest.fixture
def messenger():
    cfg = AppConfig(
        dingtalk=DingTalkConfig(
            app_key="appkey",
            app_secret="secret",
            robot_code="robot123",
            group_open_conversation_id="cidxxx",
        )
    )
    m = DingTalkMessenger(cfg)
    m.get_access_token = MagicMock(return_value="token")  # type: ignore[method-assign]
    return m


def test_send_oto_text(messenger):
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"processQueryKey": "pq1"}
        result = messenger.send_oto_text("user1", "hello")
        assert result["processQueryKey"] == "pq1"
        body = post.call_args.kwargs["json"]
        assert body["robotCode"] == "robot123"
        assert body["userIds"] == ["user1"]
        assert body["msgKey"] == "sampleText"
        assert json.loads(body["msgParam"]) == {"content": "hello"}


def test_send_oto_text_markdown_table(messenger):
    content = "### 标题\n\n| 模型 | 次数 |\n| --- | ---: |\n| m1 | 1 |"
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"processQueryKey": "pq2"}
        messenger.send_oto_text("user1", content)
        body = post.call_args.kwargs["json"]
        assert body["msgKey"] == "sampleMarkdown"
        assert json.loads(body["msgParam"]) == {"title": "标题", "text": content}


def test_send_oto_text_help_summary_uses_markdown(messenger):
    content = (
        "## 可用命令\n\n"
        "### 提交与查询\n\n"
        "| 命令 | 格式 | 说明 |\n"
        "| :--- | :--- | :--- |\n"
        "| 状态 | `状态` | 查看提交进度 |\n\n"
        "> 发送 **帮助 <命令名>** 查看详细说明。"
    )
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"processQueryKey": "pq3"}
        messenger.send_oto_text("user1", content)
        body = post.call_args.kwargs["json"]
        assert body["msgKey"] == "sampleMarkdown"
        assert json.loads(body["msgParam"]) == {"title": "可用命令", "text": content}


def test_send_oto_text_usage_llm_style_uses_markdown(messenger):
    """Agent 改写后的用量明细：列表 + 行内加粗 + ---，原先启发式会误判为纯文本。"""
    content = (
        "好的，来看看你当前周期的用量明细\n\n"
        "---\n\n"
        "** 📮 user@example.com**\n"
        "周期：6/25 ~ 7/24\n"
        "- 总次数：**1,126 次**\n"
        "- 总 Token：**5.62 亿**\n"
        "- 总费用：**$0.53**\n"
        "- 主要模型：composer-2.5（61.4%）"
    )
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"processQueryKey": "pq4"}
        messenger.send_oto_text("user1", content)
        body = post.call_args.kwargs["json"]
        assert body["msgKey"] == "sampleMarkdown"
        param = json.loads(body["msgParam"])
        assert param["title"] == "好的，来看看你当前周期的用量明细"
        assert param["text"] == content


def test_reply_session_text_markdown(messenger):
    content = "## 可用命令\n\n- **额度** — 查看额度"
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        messenger.reply_session_text("https://example.com/hook", content, at_user_id="u1")
        body = post.call_args.kwargs["json"]
        assert body["msgtype"] == "markdown"
        assert body["markdown"]["title"] == "可用命令"
        assert body["at"] == {"atUserIds": ["u1"]}


def test_send_group_text_at_all_prefix(messenger):
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {}
        messenger.send_group_text("截止提醒", at_all=True)
        body = post.call_args.kwargs["json"]
        assert "【@所有人】" in json.loads(body["msgParam"])["content"]


def test_download_message_file(messenger, tmp_path):
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post, patch(
        "pulse.channels.dingtalk.messenger.requests.get"
    ) as get:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"downloadUrl": "https://example.com/f.csv"}
        get.return_value.raise_for_status = MagicMock()
        get.return_value.content = b"Date,Model\n"
        dest = tmp_path / "f.csv"
        messenger.download_message_file("code1", dest)
        assert dest.read_bytes() == b"Date,Model\n"
