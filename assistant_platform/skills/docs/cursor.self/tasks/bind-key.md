---
name: 绑定/解绑 Cursor Key
summary: 绑定或解绑 Cursor API Key 以开启/关闭自动同步。
audience: [member]
when_to_use:
  - 用户要绑定/解绑 Cursor API Key
  - 私聊发送 crsr_ 开头的 Key
---

## 绑定 / 解绑 Cursor Key

**绑定：** 用户发送 `绑定 cursor key crsr_…` 或私聊直接发 `crsr_` 开头 Key 时，调用 tool `cursor_key_bind`。

**解绑：** 用户说「解绑 cursor …」时，调用 tool `cursor_key_unbind`。

Key 相关操作建议私聊；私聊中 tool 返回的 Key 须原样完整展示。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`。

1. 确认绑定成功；可点明账号标识（若 `result` 含）。
2. 勿编造 Key 明文；绑定成功通常不回显完整 Key。
3. 失败时才说明 error/`user_message`。
