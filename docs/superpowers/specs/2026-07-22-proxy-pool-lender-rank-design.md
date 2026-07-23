# 代理池借用推荐排序 + FastRepo 401 豁免

日期：2026-07-22  
状态：已确认（用户批准方案 A / A / A / A，并同意开干）

关联：`docs/superpowers/specs/2026-07-22-proxy-pool-account-level-design.md`、借用推荐 `pulse/tool_center/burn_rate.py::recommend_lenders`

## 1. 目标

1. 代理池选号优先消化「快作废、有剩余额度」的账号，与 Web 创建借用 Key 的推荐逻辑同源。
2. FastRepo / Repository 索引握手的 HTTP 401/403 不再 `markBad`，避免误烧仍可用于 Agent Run 的主 Key。

## 2. 池排序（Pulse 控制面）

| 点 | 决策 |
|---|---|
| 排序位置 | `GET /api/internal/v1/proxy/pool` |
| 算法 | 复用 `recommend_lenders` + `LoanSelectionConfig` |
| 硬过滤 | 不合格账号（耗尽 / 覆盖时长不足 / 在借达上限 / 无快照）整户不出池 |
| 空池 | 过滤后为空 → `{credentials: []}`，不回退 |
| 协议 | 仍为 `{credential_id, api_key}`；顺序即推荐序 |
| 同账号多 primary | 按账号推荐序分组；组内保持 `bound_at` 升序 |

候选组装：对当前已入池候选凭证所属账号，取最新 `AccountQuotaSnapshot`、`renews_on`、active loan 数，构造 `LenderCandidate`，再调用 `recommend_lenders`。

Go：`ReplaceFromPulse` 按返回顺序重建列表，并将 **`cur` 置 0**（新 exchange 优先高分账号；已绑定会话仍 sticky 至轮换）。

## 3. FastRepo markBad 豁免（Go 数据面）

路径匹配（大小写敏感子串即可，与现网 path 一致）：

- 含 `FastRepo`
- 或含 `RepositoryService`

对这些路径：HTTP/Connect 分类为 `failAuth` 时 **不** `mark` / 不轮换，原样把上游响应回给客户端。

其它路径（含 exchange 401、Agent Run 鉴权失败）行为不变。

## 4. 测试

- Python：双账号池按 urgency 排序；耗尽账号被硬过滤；无快照不出池。
- Go：`isNonFatalAuthPath`；FastRepo 401 不 markBad；`ReplaceFromPulse` 后 `cur==0`。

## 5. 非目标

- 不在 Go 内重实现 burn rate。
- 不改变借用 Key 出池规则（仍仅 primary）。
- 不放宽非 FastRepo/Repository 路径的 401 处理。
