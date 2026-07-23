---
name: 我的 Cursor
summary: 查看本人 Cursor 用量、额度与提交记录；绑定或解绑 API Key。
audience: [member]
when_to_use:
  - 用户想了解「我的 Cursor」整体功能说明（具体查提交/用量/额度/绑定解绑 Key 见对应任务文档）
---

# 我的 Cursor

涵盖查本人提交、用量、额度，以及绑定/解绑 Cursor API Key。

## 常用说法

- `我的` / `/my`：查看当前账期提交记录
- `我的用量`：查看 Cursor 用量摘要
- `额度` / `我的额度`：查看剩余额度
- `绑定 cursor key …`：绑定 Key 开启自动同步
- `解绑 cursor …`：解绑 Key

## 执行提示

- 查提交：调用 tool `submission_self_read`
- 查本人用量：调用 tool `usage_self_read`
- 查额度：调用 tool `quota_self_read`
- 绑定 Key：调用 tool `cursor_key_bind`
- 解绑 Key：调用 tool `cursor_key_unbind`

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
