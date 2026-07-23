---
name: 我的借用
summary: 查看本人当前进行中的 Key 借用详情（创建时间、用量、Key 等）。
audience: [member]
when_to_use:
  - 用户问「我的借用」「借用状态」「我借的 Key」
---

## 我的借用

**说法：** `我的借用` / `借用状态`

调用 tool `key_loan_self_read`。

### 展示版式（按 tool `result` 排版，禁止编造）

tool 成功时只读 `result`（`schema_version=1`）；`user_message` 为空，不要依赖它。

1. **空借用**：`empty_reason == "no_active_loan"` 或 `loans` 为空 →  
   `你当前没有进行中的 Key 借用。`
2. **有借用**：遍历 `loans`（可能多条，不得漏），每条说明：
   - 创建时间：`created_at`
   - 用量：若 `usage_source == "proxy"` → 一行摘要  
     `用量：{proxy_request_count} 次 · {proxy_total_tokens} tokens · ≈${proxy_cost_usd}（Proxy 精确计量）`；  
     若有 `remaining_headroom_pct` 可附「还能用：x%」。  
     **禁止**展开模型明细表。
   - 无 proxy（`usage_source == "quota_approx"` 或无 proxy 字段）→ `近似消耗：${approx_borrowed_usd}`
   - 重置日自动回收：是/否（`auto_revoke_on_reset`）；自动回收日：`loan_expires_on`
3. **禁止展示**：`lender_name`、`source_identifier`、借出人、来源账号。
4. **API Key（私聊）**：若 `api_key` 有值，**必须完整原样展示**，禁止掩码或截断；若 `api_key_unavailable` 则说明暂无法读取。`requires_proxy` 为真时可提示须配置 HTTPS_PROXY。
5. 文末可提示：归还可发送「归还 Key」。
6. **禁止**：编造未出现在 `result` 的费用或 Key；不得只展示第一条而省略其余。
