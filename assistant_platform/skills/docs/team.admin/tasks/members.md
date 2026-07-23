---
name: 成员管理
summary: 管理员查看或维护催办成员名单（新增/移除）。
audience: [admin]
when_to_use:
  - 管理员查看或维护成员催办名单
---

## 成员管理

- `成员` — 查看 active 成员名单
- `成员 添加 <userid> <姓名>` — 加入催办名单
- `成员 移除 <userid>` — 设为 inactive

调用 tool `members_manage`。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
