from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from pulse.bot.dingtalk.files import (
    extract_file_attachment,
    incoming_message_type,
    normalize_incoming_text,
)
from pulse.bot.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig, DingTalkConfig


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
    with patch("pulse.bot.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"processQueryKey": "pq1"}
        result = messenger.send_oto_text("user1", "hello")
        assert result["processQueryKey"] == "pq1"
        body = post.call_args.kwargs["json"]
        assert body["robotCode"] == "robot123"
        assert body["userIds"] == ["user1"]
        assert json.loads(body["msgParam"]) == {"content": "hello"}


def test_send_group_text_at_all_prefix(messenger):
    with patch("pulse.bot.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {}
        messenger.send_group_text("截止提醒", at_all=True)
        body = post.call_args.kwargs["json"]
        assert "【@所有人】" in json.loads(body["msgParam"])["content"]


def test_download_message_file(messenger, tmp_path):
    with patch("pulse.bot.dingtalk.messenger.requests.post") as post, patch(
        "pulse.bot.dingtalk.messenger.requests.get"
    ) as get:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"downloadUrl": "https://example.com/f.csv"}
        get.return_value.raise_for_status = MagicMock()
        get.return_value.content = b"Date,Model\n"
        dest = tmp_path / "f.csv"
        messenger.download_message_file("code1", dest)
        assert dest.read_bytes() == b"Date,Model\n"
