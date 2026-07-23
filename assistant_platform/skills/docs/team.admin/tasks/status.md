---
name: 状态
summary: 管理员查看当前账期各成员的提交/催办进度。
audience: [admin]
when_to_use:
  - 管理员查看团队当前提交/催办状态
---

## 状态

**说法：** `状态`（同义：`/status`）

调用 tool `submission_status_read`。查看当前账期各 active 成员提交进度。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
