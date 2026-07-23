---
name: 借用列表与撤销
summary: 管理员查看团队借用列表，或强制撤销某条借用。
audience: [admin]
when_to_use:
  - 管理员查看团队借用列表或撤销借用
---

## 借用列表与撤销

管理员查看团队借用情况时，调用 tool `key_loan_list`。

需要强制收回时，调用 tool `key_loan_revoke`。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
