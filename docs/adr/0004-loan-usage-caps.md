# ADR-0004：借用 Key 的滚动用量封顶

> **部分取代**：借用封顶与 Cursor `pk_` 的 5h/7d 窗口已迁移为成员级统一用量规则与可选预付余额，见 [ADR-0006](0006-member-membership-credit.md)。下文保留历史决策与实现细节供对照；新功能请以 ADR-0006 为准。

- 状态：已实现（2026-10-02 修订为多条「或」规则）
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

5. **默认不封顶。** 一条规则都没有 = 与今天行为一致。
6. **一个借用可以有多条规则，规则之间是「或」。** 每条规则是「周期 + Auto 或 API + 整数美元」。例如「5 小时 Auto $10」或「7 天 Auto $50」：请求所属桶里任一规则达到上限就拦截该桶。同一周期、同一桶只能有一条。Auto 与 API 仍分开，所以也可以同时限制两个桶、两个窗口。
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

`key_loans.usage_cap_rules`（JSON，可空）存规则列表。空列表 = 不限制。每一项：

| 字段 | 含义 |
| --- | --- |
| `period` | `5h` / `week` / `month` |
| `pool` | `auto` 或 `api` |
| `limit_cents` | 该桶在该窗口内的上限（美分），至少 100（1 美元） |

任一与请求桶相同的规则达到上限即拒绝该桶。`cursor_direct` 借用拒绝写入封顶。

早期三列 `usage_cap_period`、`auto_cost_limit_cents`、`api_cost_limit_cents` 只读旧数据：`usage_cap_rules is None` 时按「同一周期、两桶各自可选上限」折成规则。新写入把规则放进 JSON，并把这三列清空。迁移把尚未写入 JSON 的旧行回填成规则列表。

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

- 只触及一条：`【小脉借用】Auto 额度已用尽（滚动 7 天 $10 / $10（约 1 天 5 小时后恢复，2026-10-09 14:00 北京时间））。请改用 API 模型，或等到恢复后再用 Auto。`
- 同一桶触及多条：`【小脉借用】Auto 已触及多条限制（任一达到即停）：滚动 5 小时 …；滚动 7 天 …。`
- 仅 API 超限且 Auto 仍可用：对称，提示改用 Auto / Composer。
- 另一桶「仍可用」包括该桶没有规则，或有规则但窗口内未超限。没有规则表示可以改用那个桶，提示里要写出切换建议。
- 另一桶也已超限：不提示切换。
- 已用不是整数美元时，金额写到分（`$10.50`），上限仍是整数美元。
- 同一请求桶有多条超限时，响应里的 `resets_at` 取这些规则里最晚的一个（该桶要等全部超限规则都回落才能再用）。

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

发放（`LoanKeyBody`，指定账号与账号池轮换都接受）带 `usage_caps`。省略或 `[]` 表示不限制。每一项：

```json
{ "period": "5h", "pool": "auto", "cost_usd": 10 }
```

`cost_usd` 为整数且 ≥ 1。重复的 `period + pool` 返回 400。

`PATCH /api/v2/loans/{loan_id}/usage-cap`（`accounts:write`）仍可整表替换或清空。清空用显式 `clear: true`。管理界面不再单独打开这个入口。

调整出借方式 `POST /api/v2/loans/{loan_id}/reassign-source` 接受可选的 `usage_caps`。缺省表示不改规则。给出数组（含空数组）就整表替换。出借方式没有变化时只保存规则，不轮换 Key，也不返回「已是账号池轮换」。`cursor_direct` 拒绝写入。

借用列表 payload 用 `usage_caps`：每条含 `period`、`pool`、`limit_cents`、`cost_usd`、`used_cents`、`exceeded`、`resets_at`。没有规则时为 `[]`。

界面：

- 「为成员分配 Key」和「调整出借方式」里，用量限制默认不展开。点「+」才增加一行（周期、Auto/API、美元）。没有行就不显示输入框。
- 有多行时说明：多条是「或」，任一达到就限制对应的 Auto 或 API。
- 用量限制和出借方式在「调整出借方式」里一起保存。借用记录上不再单独放封顶按钮。
- 借用记录和「我的借用」用同一套只读条展示：按 Auto / API 分组，每条是周期、金额和金额下的短进度。列宽固定，不随表格拉开。同一类型的多条之间标「或」。没有规则时显示「不限」。悬停可看滚动窗口、精确金额，以及接近或达到上限后的恢复时间。
- 自动分配不展示当前入池账号排序。只保留一句「使用中轮换、不锁定账号」，以及账号池为空时的提示。
- 自助借 Key 不写规则。

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
- 发放、PATCH 与「调整出借方式」：重复的周期+桶 400；`cursor_direct` 400；清空后 `usage_caps` 为 `[]`。账号池轮换在出借方式不变时仍可只改规则。
- 同一桶两条规则是「或」：5 小时 $10 已满则拦截，即使 7 天 $50 仍有余量；5 小时窗口滑出后放行。
- Go：`loan_alias` / `loan_pool` 的 Run 在 `limited` 时 429 且 body 含 Python message；`loan_passthrough` 不发起校验请求。

## 后果

借用人看到的是代理估算，可能与 Cursor 设置页上的百分比不完全一致。滚动 30 天会跨过账号真实重置日，封顶不会跟着 Cursor 账单周期清零。非 Run 请求不拦截，极端情况下账本仍会增加，下一次 Run 才被挡住。
