---
name: 我的（查看提交记录）
summary: 查看当前账期最近一次 Cursor 提交时间与渠道。
audience: [member]
when_to_use:
  - 用户说「我的」或「/my」查看当前账期提交记录
---

## 我的

**说法：** `我的`（同义：`/my`）

查看你在当前账期的最近一次提交时间与渠道。调用 tool `submission_self_read`。

- 包含 Cursor 自动同步与非 Cursor 手工上报。
- 若尚无记录，会明确提示。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
