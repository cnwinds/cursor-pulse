---
name: 其他 AI 工具用量
summary: 上报智谱/MiniMax/Codex 等用量；支持文本或私聊截图识别。
audience: [member]
when_to_use:
  - 用户要上报智谱/MiniMax/Codex 等非 Cursor 用量
  - 用户发送相关控制台截图（私聊）
---

# 其他 AI 工具用量

上报智谱、MiniMax、Codex 等非 Cursor 工具用量。

## 文本上报

**格式：** `上报 <工具名> <数值> [单位]`

示例：`上报 智谱 85`、`用量 minimax 12000 calls`

调用 tool `usage_manual_submit`。提交后 **直接入库**，无需管理员审核。

## 截图上报

私聊发送智谱/MiniMax/Codex 控制台截图，调用 `usage_manual_submit`（截图识别路径）。

**注意：** Cursor 用量请绑定 API Key，不要用此方式上报。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
