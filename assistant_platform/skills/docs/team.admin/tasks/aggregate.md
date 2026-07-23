---
name: 聚合
summary: 管理员按账期重新聚合团队用量汇总数据。
audience: [admin]
when_to_use:
  - 管理员要执行账期用量聚合
---

## 聚合

**说法：** `聚合 [账期]`（同义：`/aggregate`）

调用 tool `usage_aggregate`。按账期重算团队用量汇总。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
