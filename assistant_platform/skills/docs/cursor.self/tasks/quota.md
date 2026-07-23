---
name: 我的额度
summary: 查看本人剩余 Cursor 额度百分比。
audience: [member]
when_to_use:
  - 用户问「额度」「还剩多少」「额度够不够」
---

## 查额度

用户问「额度多少」「还剩多少」时，调用 tool `quota_self_read`。

成功后用自然语言说明剩余额度与账期；若额度偏紧，可建议查看 `key.loan` 技能说明是否适合借临时 Key。

### 展示版式（按 tool `result` 排版，禁止编造数字）

tool 成功时 `user_message` 为空；只读 `result`（`schema_version=1`）。

1. **空数据**：若 `empty_reason == "no_cursor_account"` 或 `accounts` 为空 → 说明尚未绑定 Cursor 账号。
2. 按账号列出总额度使用百分比（`total_pct` / Auto / API 等字段）；数字须来自 `result`。
3. 失败时才说明 error/`user_message`。
