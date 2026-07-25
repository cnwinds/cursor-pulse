"""Backward-compatible re-export — prefer ``pulse.channels.feishu``. """

from __future__ import annotations

from pulse.channels.feishu.messenger import FeishuMessenger

__all__ = ["FeishuMessenger"]
