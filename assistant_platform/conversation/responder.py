from __future__ import annotations


def simple_reply(text: str) -> str:
    """Rule-based Chinese helper when Pulse chat is unavailable."""
    preview = text.strip()
    if len(preview) > 80:
        preview = preview[:80] + "…"
    commands = "额度（查询额度）、绑定（绑定 Cursor Key）"
    if preview:
        return f"收到：{preview}。我是小脉助手，可用命令：{commands}。直接发送命令即可。"
    return f"我是小脉助手，可用命令：{commands}。直接发送命令即可。"
