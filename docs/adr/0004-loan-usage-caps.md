# ADR-0004：借用 Key 的滚动用量封顶

- 状态：已审核，待实现
- 日期：2026-10-02
- 相关：`pulse/proxy/authorize.py`、`pulse/web/internal_proxy_api.py`、`pulse/web/quota_api.py`、`proxy/mitm.go`、`web-admin/src/views/LoansView.vue`、`CONTEXT.md`

## 背景

管理员在「为成员分配 Key」时只能控制借给谁、指定账号还是账号池轮换，不能限制这位借用人自己在一段时间内用掉多少 Auto 额度、多少 API 额度。出借账号的 Snapshot Headroom 是账号级保护，挡不住单个借用人在周期内打满。

`pk_` 已有滚动 5 小时 / 7 天的**总费用**窗，在业务请求上返回 429，且不在 exchange 阶段把 Key 判失效。Key Loan（`pka_`）没有对应能力。本次只加借用方封顶，不改 `pk_` 窗口，也不改选号打分。

## 已审核决策

产品确认：

1. **周 = 滚动 7 天**，与现有 `WINDOW_7D` 同一长度。
2. **月 = 滚动 30 天**，不是自然月，也不是出借账号的 Cursor billing cycle。
3. **BYOK 不计入、也不拦截。**
4. **`cr*` 直连透传（`loan_passthrough` / `delivery_mode=cursor_direct`）不计入、也不拦截。** 只覆盖经代理记账的 `pka_`：`loan_alias` 与 `loan_pool`。

审核时另定的实现约束（实现时不得改口径）：

5. **默认不封顶。** 三个配置都为空 = 与今天行为一致。
6. **一个借用只有一个记账周期，Auto 与 API 上限分开。** 不支持「Auto 按 5 小时、API 按 30 天」。
7. **费用单位沿用代理账本的估算美分**（`ProxyKeyUsage.cost_cents`），不是 Cursor 发票。管理端输入**整数美元**，最小 1 美元；空表示该桶不限制。换算沿用 `usd_to_cents` / `cents_to_usd`（`1 USD = 100 cents`）。
8. **分桶不得调用 `quota_pool_for_model`。** 该函数把 BYOK 算进 `auto`，与决策 3 相反。封顶用单独的 `loan_usage_cap_pool`。
9. **空模型算 Auto。** 与 Go `quotaPoolForModel("") == auto` 一致，避免无模型请求绕过 Auto 封顶。能识别为 BYOK 的模型名返回「不计入」。
10. **Cursor 目录里的小写连字符第三方模型（如 `glm-5.2-high`）计入 API 桶。** 它们走套餐 API 额度，不是 BYOK。只有 `is_likely_byok_model` 为真的名字跳过。
11. **只在 `AgentService/Run` 上拦截**，且必须在上游 `RoundTrip` 之前。其它路径没有可靠模型，若一律按 Auto 拦截会误伤非对话请求。这些路径即使产生账本行，仍计入对应桶，但本次不拦。
12. **Exchange / 登录不因封顶失败。** 与 `pk_` 的 `window_limited` 相同，避免 Cursor 显示 “API key is invalid”。
13. **校验失败时关闭放行（fail closed）。** 封顶借用的 Run 在内部校验请求失败时返回 503，文案说明稍后重试，不把请求送到上游。
14. **在途并发可以略微超限。** 用量在回合结束后异步入账，与 `pk_` 窗口相同，不在本次做预扣。
15. **改封顶配置不轮换 Key。** 借用人继续用原来的 `pka_`。
16. **自助借 Key 不写封顶**（保持默认不限制）。只有管理端分配与后续修改会写入。

## 决策

### 配置

`key_loans` 增加三列，均可空：

| 列 | 类型 | 含义 |
| --- | --- | --- |
| `usage_cap_period` | `VARCHAR(16)` | `5h` / `week` / `month`；空 = 不启用 |
| `auto_cost_limit_cents` | `INTEGER` | Auto 桶上限（美分）；空 = 该桶不限制 |
| `api_cost_limit_cents` | `INTEGER` | API 桶上限（美分）；空 = 该桶不限制 |

启用条件：`usage_cap_period` 非空，且至少一个上限非空。API 拒绝只填周期或只填上限。`cursor_direct` 借用拒绝写入封顶。

窗口长度：

| 周期 | 长度 |
| --- | --- |
| `5h` | `timedelta(hours=5)` |
| `week` | `timedelta(days=7)` |
| `month` | `timedelta(days=30)` |

统计 `ProxyKeyUsage.loan_id = 该借用` 且 `ts > now - 窗口`（左开区间）且 `usage_cap_pool` 等于该桶的 `cost_cents` 之和。达到或超过上限即拒绝。左开是为了让下面的 `resets_at` 在该时刻已经不再计入被挤出的那一条。

### 分桶列

`proxy_key_usages.usage_cap_pool`：`auto` | `api` | `skip`，可空。

`loan_usage_cap_pool(model) -> "auto" | "api" | None`：

- 空白模型 → `auto`
- `is_likely_byok_model` → `None`（落库写 `skip`）
- `is_auto_composer_model` → `auto`
- 其余 → `api`

`record_usages` 在**借用行**写入该列。`pk_` 行留空。迁移补列后，用同一函数回填已有 `loan_id` 行的空值（幂等，分批）。校验 SQL 只汇总非空 `usage_cap_pool`；回填完成前，窗口内仍为 NULL 的借用行在 Python 里用同一函数现算并计入，避免刚打开封顶时历史用量被漏掉。

### 重置时间

滚动窗口，谓词与统计相同（`ts > now - 窗口`）。超限后 `resets_at` 是已用重新**低于**上限的最早时刻。按费用从最旧一条开始移出，而不是固定用最旧一条：

```python
def usage_resets_at(events, limit, window):
    # events: 当前窗口内 (ts, cents)，按 ts 升序。调用方已确认 sum >= limit。
    dropped = 0
    total = sum(cents for _, cents in events)
    for ts, cents in events:
        dropped += cents
        if total - dropped < limit:
            return ts + window
    return events[-1][0] + window
```

移出某条后剩余和第一次 `< limit`，`resets_at` 就是该条的 `ts + 窗口`。单条本身已达上限时，结果就是该条 `ts + 窗口`。

对用户展示为东八区本地时间，以及距现在的简短倒计时。代理报文用一句中文，由 Python 生成。

### 内部校验

`POST /api/internal/v1/proxy/loan-usage-cap`，鉴权与 `/proxy/authorize` 相同。

请求：

```json
{ "loan_id": "...", "model": "composer-2.5" }
```

响应 HTTP 200（拒绝与否放在 body；由 Go 决定对 Cursor 返回 429 或放行）：

```json
{
  "status": "ok",
  "reason": null,
  "pool": null,
  "period": null,
  "used_cents": 0,
  "limit_cents": null,
  "resets_at": null,
  "other_pool": null,
  "other_pool_open": false,
  "message": ""
}
```

`status=limited` 时：

- `reason` 固定 `loan_usage_cap_exceeded`
- `pool` 为 `auto` 或 `api`
- `other_pool` 为另一桶；`other_pool_open` 为真表示另一桶没有上限，或窗口内已用低于上限
- `message` 为给 Cursor 用户看的一整句中文，Go 原样放入 429，不再拼英文

文案：

- 仅 Auto 超限且 API 仍可用：`【小脉借用】Auto 额度已用尽（$10 / $10，滚动 7 天）。约 1 天 5 小时后恢复（2026-10-09 14:00 北京时间）。请改用 API 模型，或等到恢复后再用 Auto。`
- 仅 API 超限且 Auto 仍可用：对称，提示改用 Auto / Composer。
- 另一桶也不可用，或另一桶未配置因而无从切换：不提示切换，只说明本桶已用尽与恢复时间。
- 两桶都超限时，当前请求所属桶用该桶自己的 `resets_at`。

`reason` 在放行时可为 `cap_disabled`、`not_counted`（BYOK）、`not_applicable`（非代理借用或无此借用）。这些都是 `status=ok`。

Go 只对 `binding.Mode` 为 `loan_alias` 或 `loan_pool`、且路径包含 `AgentService/Run` 的请求调用。调用点在 `resolveQuotaPool` 之后、选凭证 / `RoundTrip` 之前。传入 `findModelName` 得到的**原始模型字符串**，不要传 `quotaPoolForModel` 的结果。`loan_passthrough` 不调用。

超限时 Go 返回：

```json
{
  "code": "resource_exhausted",
  "message": "<Python message>"
}
```

HTTP 429。校验请求本身失败（网络、非 200、超时）时返回 503，body 文本：`cursor-pulse-proxy: 借用用量校验暂不可用，请稍后重试`。不把该失败缓存成会话级永久拒绝。

### 管理 API 与界面

发放（`LoanKeyBody`，指定账号与账号池轮换都接受）：

- `usage_cap_period`: `5h` | `week` | `month` | 省略
- `auto_cost_usd`: 整数 ≥ 1 或省略
- `api_cost_usd`: 整数 ≥ 1 或省略

`PATCH /api/v2/loans/{loan_id}/usage-cap`（`accounts:write`）修改或清空。清空用显式 `clear: true`，避免「缺字段 = 不修改」和「缺字段 = 清空」混在一起。请求体：

```json
{ "clear": false, "usage_cap_period": "week", "auto_cost_usd": 10, "api_cost_usd": null }
```

`clear: true` 时忽略另外三个字段，三列都置空。`api_cost_usd: null` 在 `clear: false` 时表示该桶不限制。

借用列表与详情 payload 增加：

- `usage_cap_period`
- `auto_cost_limit_cents` / `api_cost_limit_cents`
- `auto_cost_usd` / `api_cost_usd`（整数美元或 null）
- `usage_cap_auto_used_cents` / `usage_cap_api_used_cents`（当前窗口；未启用封顶时为 null）
- `usage_cap_resets_at`：若任一已配置的桶已超限，取这些超限桶里最早的 `resets_at`；否则 null

「为成员分配 Key」对话框增加「用量封顶（可选）」：

- 记账周期：不限制（默认）/ 5 小时 / 滚动 7 天 / 滚动 30 天
- Auto 上限（美元，可空）
- API 上限（美元，可空）
- 说明：只统计经本代理上报的套餐用量；BYOK 与直连 Cursor Key 不计入；超出后 Cursor 对话会提示恢复时间，可改用另一桶模型。

借用记录行提供修改入口（同一组字段，可清空）。「我的借用」只读展示周期与两桶已用/上限，不提供编辑。

### 明确不做

- 不改 `pk_` 的 5h/7d 总费用窗。
- 不按 token 或请求次数封顶。
- 不把封顶写入 Cursor 账号，也不改变 Credential Pool 入池与打分。
- 不在钉钉/飞书推送「即将用尽」。
- 不拦截非 `AgentService/Run` 的代理请求。

## 测试

- 三档窗口边界：窗口外的一行不计入；窗口内达到上限则拒绝；再早一行掉出后放行。
- 只限制 Auto 时，API 模型放行，Composer/auto/空模型拒绝。
- BYOK 形态模型（`is_likely_byok_model`）不计入也不拒绝；`glm-5.2-high` 计入 API。
- `loan_pool` 下两个 credential 的用量都算在同一个 `loan_id`。
- `loan_passthrough` 不走校验函数的拒绝路径（函数对非 alias 借用返回 ok / not_applicable；Go 不调用）。
- 未配置封顶的借用始终 ok。
- 重置时间算法用固定时间夹具覆盖「多条小额」和「单条就超限」。
- 发放与 PATCH 的校验：半套配置 400；`cursor_direct` 400；清空后列表回到 null。
- Go：`loan_alias` / `loan_pool` 的 Run 在 `limited` 时 429 且 body 含 Python message；`loan_passthrough` 不发起校验请求。

## 后果

借用人看到的是代理估算，可能与 Cursor 设置页上的百分比不完全一致。滚动 30 天会跨过账号真实重置日，封顶不会跟着 Cursor 账单周期清零。非 Run 请求不拦截，极端情况下账本仍会增加，下一次 Run 才被挡住。
