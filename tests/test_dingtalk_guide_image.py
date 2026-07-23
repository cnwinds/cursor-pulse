from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pulse.channels.dingtalk.guide_image import (
    DEFAULT_GUIDE_IMAGE,
    resolve_guide_image_path,
    save_guide_image_override,
    should_attach_bind_guide_image,
)
from pulse.channels.dingtalk.messenger import DingTalkMessenger
from pulse.config import AppConfig, DingTalkConfig


def test_should_attach_bind_guide_image():
    assert should_attach_bind_guide_image("你还有 1 个 Cursor 账号未绑 Key，请先绑定后再申请。\n发送：绑定 cursor 你的邮箱@c.com crsr_...")
    assert not should_attach_bind_guide_image("借 Key 失败：额度尚充足")


def test_resolve_guide_image_path_prefers_override(tmp_path):
    override = tmp_path / "assets" / "cursor_bind_key_guide.png"
    override.parent.mkdir(parents=True)
    override.write_bytes(b"override")
    assert resolve_guide_image_path(tmp_path) == override


def test_resolve_guide_image_path_falls_back_to_default():
    assert resolve_guide_image_path("/nonexistent") == DEFAULT_GUIDE_IMAGE


def test_save_guide_image_override(tmp_path):
    source = tmp_path / "incoming.png"
    source.write_bytes(b"png-data")
    saved = save_guide_image_override(tmp_path, source)
    assert saved.read_bytes() == b"png-data"


@pytest.fixture
def messenger():
    cfg = AppConfig(
        dingtalk=DingTalkConfig(
            app_key="appkey",
            app_secret="secret",
            robot_code="robot123",
        )
    )
    m = DingTalkMessenger(cfg)
    m.get_access_token = MagicMock(return_value="token")  # type: ignore[method-assign]
    return m


def test_upload_image_media_id_caches_by_mtime(messenger, tmp_path):
    image = tmp_path / "guide.png"
    image.write_bytes(b"png")

    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {"errcode": 0, "media_id": "@abc"}
        media_id = messenger.upload_image_media_id(image)
        assert media_id == "@abc"
        media_id_again = messenger.upload_image_media_id(image)
        assert media_id_again == "@abc"
        assert post.call_count == 1


def test_send_oto_image(messenger):
    with patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {}
        messenger.send_oto_image("user1", "@lADPM3FGD4G7ntvNAWD")
        body = post.call_args.kwargs["json"]
        assert body["msgKey"] == "sampleImageMsg"
        assert json.loads(body["msgParam"]) == {"photoURL": "@lADPM3FGD4G7ntvNAWD"}


def test_send_oto_image_file_falls_back_to_file(messenger, tmp_path):
    image = tmp_path / "guide.png"
    image.write_bytes(b"png")

    with patch.object(messenger, "upload_image_media_id", return_value="@abc"), patch.object(
        messenger, "send_oto_image", side_effect=RuntimeError("image failed")
    ), patch("pulse.channels.dingtalk.messenger.requests.post") as post:
        post.return_value.raise_for_status = MagicMock()
        post.return_value.json.return_value = {}
        messenger.send_oto_image_file("user1", image)
        body = post.call_args.kwargs["json"]
        assert body["msgKey"] == "sampleFile"
