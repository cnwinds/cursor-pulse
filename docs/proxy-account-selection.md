# 代理自动选号：分层与规则

MITM 上「用哪个 Cursor 凭证服务一次请求」由 **两层独立决策** 叠加完成。混用两层规则（或只在一层过滤、另一层不过滤）会导致：换票成功但业务请求失败、座位顾问与 Go 池状态 ping-pong、池内明明有可用号却报 `all API keys exhausted`。

## 两层职责

| 层 | 负责方 | 输入 | 输出 | 不做什么 |
|----|--------|------|------|----------|
| **Credential Pool 入池** | Pulse `pool_board` | 快照 `auto_pct` / `api_pct`、burn/coverage | 有序凭证列表给 Go 热更新 | 不绑定 CLI 会话；不区分具体模型 |
| **Concurrent Seat（座位）** | Pulse `occupancy` + authorize | 排名、`max_concurrent_users`、当前凭证 | `assigned_credential_id` / `blocked_credential_ids` | **不读** Quota Pool 余量；只管同一账号同时几个人 |
| **Quota pick（配额选号）** | Go `Pool` + `StickySelect` | 请求模型 → Quota Pool（auto/api/**unknown**）、runtime 耗尽标记 | 带 JWT 的 `keyEntry` | 不替代 Pulse 的并发上限（除非 seat advice fail-open） |

术语与 CONTEXT.md 一致：**Credential Pool Intake** 用 OR（任一桶 Snapshot Headroom）；**unknown Quota Pool**（模型未知 / BYOK 等）用 AND（两桶都要 &lt;100%），与 Go `snapshotQuotaOK` 一致。

## 端到端路径

### 1. 换票（exchange）

1. Go → `POST /api/internal/v1/proxy/authorize`（通常无 `current_credential_id`）。
2. Pulse：`authorize_status` → `apply_seat` 按 **排名 + 并发** 分配座位。
3. Go：`exchangeAdvised` 对 `assigned_credential_id` **必须** 通过 `availableFor(quotaPoolUnknown)`（换票时尚无模型，保守按 unknown）；不通过则 `release_current` + `skip_credential_ids` 向 Pulse 要下一座，或回落 `tokenForQuotaPool(unknown)`。

### 2. 业务请求（MITM）

1. 从 CLI JWT 取 `SessionBinding`，按路径/模型解析 `quotaPool`（`resolveQuotaPool`）。
2. `StickySelect.Select`：
   - sticky 仍 `availableFor(pool)` → `tokenForCredential`（仅换票；配额已在上面判断）。
   - 否则 `release_current` + **`skip_credential_ids`（配额不可用）** 问 Pulse 下一座；Go 再验 `availableFor(pool)`。
   - Pulse 仍给不出 **且** 存在配额可用号 → **本地** `nextAvailableForQuotaWithin`（与 README「顾问失败 fail-open」同 spirit，仅针对配额不匹配）。
   - Pulse 明确「并发满员且无座」且 `seat_advised=true`、分配为空 → fail-closed，不塞进满员账号。

### 3. 自动分配借用（`loan_alias` + 白名单）

与共享池相同，但 `allowed` 限制在 Pulse 下发的 `credential_ids` 内；unknown 双桶规则不变。

## `skip_credential_ids`

Go 在「座位分了、配额不够」时，把已拒绝的 credential ID 传给 authorize。Pulse `occupancy.choose` 在 `release_current` 之外 **一并跳过** 这些 ID，避免只在单次 release 下仍回到排名第一的「半满」账号。

## Switch dwell

`PROXY_STICKY_MIN_DWELL` / `min_switch_minutes` 只抑制 **配额驱动的轮转**；auth 失败、全池 unknown 不可用、顾问 fail-closed（并发）仍应立即换号或报错。

## 相关代码

- Go：`proxy/quota_state.go`、`proxy/sticky_select.go`、`proxy/pool.go`、`proxy/mitm.go`
- Pulse：`pulse/proxy/occupancy.py`、`pulse/proxy/seat_assignment.py`、`pulse/proxy/pool_board.py`
- ADR：`docs/adr/0001-auto-lender-selection.md`
