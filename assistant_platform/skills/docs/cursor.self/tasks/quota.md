---
name: 我的额度
summary: 查看本人 Cursor 与 Coding Plan（GLM / MiniMax / Kimi）额度快照。
audience: [member]
when_to_use:
  - 用户问「额度」「还剩多少」「额度够不够」
  - 用户问 GLM、MiniMax、Kimi Coding Plan 窗口用量
---

## 查额度

用户问「额度多少」「还剩多少」时，调用 tool `quota_self_read`。

成功后用自然语言说明剩余额度；Cursor 账号可提账期与 Auto/API 分项；Coding Plan 账号说明 5h / 周窗口百分比（无历史用量曲线）。若 Cursor 额度偏紧，可建议查看 `key.loan` 技能是否适合借临时 Key（GLM/MiniMax/Kimi 不支持借用）。

### 展示版式（按 tool `result` 排版，禁止编造数字）

tool 成功时 `user_message` 为空；只读 `result`（`schema_version=1`）。

1. **空数据**：若 `empty_reason == "no_account"` 或 `accounts` 为空 → 说明尚未绑定任何 AI 账号（Cursor / Coding Plan）。
2. **Cursor**（`display_mode == "cursor"`）：按账号列出 `total_pct` / Auto / API 等字段。
3. **Coding Plan**（`display_mode == "coding_plan_tiers"`）：按 `vendor_name` + `quota_tiers[]` 的 `label` / `utilization_pct` 说明窗口额度；数字须来自 `result`。
4. 失败时才说明 error/`user_message`。
