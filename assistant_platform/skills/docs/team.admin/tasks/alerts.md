---
name: 告警
summary: 管理员运行用量/额度异常规则并查看告警列表。
audience: [admin]
when_to_use:
  - 管理员要跑用量/额度告警
---

## 告警

**说法：** `告警 [账期]`（同义：`/alerts`）

调用 tool `alerts_run`。运行异常规则并输出告警列表。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
