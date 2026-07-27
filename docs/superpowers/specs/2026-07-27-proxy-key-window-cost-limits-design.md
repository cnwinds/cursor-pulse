# Proxy Key 窗口费用额度

Approved 2026-07-27.

## Goal

脉冲 Key（`pk_`）额度只保留滑动窗口费用限制：

- 近 **5 小时**费用上限
- 近 **7 天**费用上限

界面以美金整数输入（如 `$10`）；留空 = 不限；不允许 `$0`（最小 `$1`）。

## Model

| Column | Type | Notes |
|--------|------|--------|
| `window_5h_cost_limit_cents` | int nullable | `$N` → `N * 100`；null = 不限 |
| `window_7d_cost_limit_cents` | int nullable | 同上 |

Remove enforcement of: `token_limit`, `cost_limit_cents` (lifetime), `window_5h_token_limit`.

Keep columns for one release (nullable, cleared) or drop via migrate where practical; API no longer accepts/returns them as editable limits.

`mode`: always `quota` for new keys; UI drops 畅享/限额. Empty windows = unlimited.

## Authorize

If `window_5h_cost_limit_cents` set and sum(`cost_cents`) over last 5h ≥ limit → `window_limited` / `window_5h_exceeded`.

Same for 7d → `window_7d_exceeded`.

Soft reject only; do **not** suspend for window overage. `evaluate_key` stops lifetime suspend.

## API / UI

Create/update: `window_5h_cost_usd` / `window_7d_cost_usd` as optional int ≥ 1, or accept cents with validation ≥ 100. Prefer USD ints in API for clarity with UI.

Migration: add columns; `UPDATE proxy_keys SET token_limit=NULL, cost_limit_cents=NULL, window_5h_token_limit=NULL, mode='quota'`.

## Non-goals

Loan alias keys (`pka_`) unchanged. Lifetime hard caps not restored.
