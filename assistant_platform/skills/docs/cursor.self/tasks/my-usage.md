---
name: 我的用量
summary: 查看本人 Cursor 用量明细，按账号/模型拆分，支持指定月份查询。
audience: [member]
when_to_use:
  - 用户问「我的用量」或查本人 Cursor 用量明细
  - 用户指定自然月或 YYYY-MM 查询用量
---

## 我的用量

**说法：** `我的用量`；可加「自然月」或 `YYYY-MM`，例如 `我的用量 2026-06`。

调用 tool `usage_self_read`。

- 展示各 Cursor 账号用量合计及按模型拆分明细。
- 借用 Key：有 Proxy 数据时展示精确次数/Tokens/估算费用与模型表；否则回退已消耗 % / 还能用 %。
- 需已绑定 Key 或有进行中的借用。

### 展示版式（按 tool `result` 排版，禁止编造数字）

tool 成功时 `user_message` 为空；只读 `result`（`schema_version=1`）。

1. **空数据**：若 `empty_reason == "no_cursor_or_loan"` 或 `accounts` 为空 → 回复：  
   `尚未绑定 Cursor 账号，且当前无进行中的 Key 借用。`
2. **标题**：  
   - `query.mode == "calendar_month"` → `### 你的用量（自然月 {query.period}）`  
   - 否则 → `### 你的用量（当前账期）`
3. **不要画总览表**；直接按 `accounts` 分账号写明细（账号之间可用 `---` 分隔）。
4. **每个 `kind=owned` 账号**：写出 `identifier`、周期 `range_text`（`window_label`）、合计（次数 / Tokens / 费用）；再列模型表（`| 模型 | 次数 | Tokens | 费用 | 占比 |`）。可按 tokens/费用写主力模型，但数字必须来自 `result`。
5. **每个 `kind=loan` 账号**：
   - 标题只用「借用 Key」，可附 `loan.loan_created_at` 的月/日起；**禁止**展示 `lender_name`、`source_identifier` 或「借自 …」。
   - 若 `usage_source == "proxy"`（或顶层同名字段）：写「来源：Proxy 精确计量 · 费用为本地估算」；合计用 `events` / `tokens` / `cost_usd`（费用前加 `≈`）；若有 `remaining_headroom_pct` 写「还能用」；再画模型表（费用列标题用「估算费用」，金额加 `≈`）。
   - 若 `usage_source == "quota_approx"` 或无 proxy 明细：只写已消耗 %（`borrowed_quota_pct`）与还能用 %（`remaining_headroom_pct`），不要空模型表。
6. **禁止**：编造未出现在 `result` 的次数/Tokens/费用；不得合并或漏掉 `accounts` 中的账号；不得展示借出人/来源账号。
7. 文末可提示：也可发送「额度」/「我的借用」。
