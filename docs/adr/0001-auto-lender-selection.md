# ADR-0001：Auto Lender 用 Jev 主判、算法分保底

- 状态：已采纳
- 日期：2026-09-20
- 相关：`pulse/tool_center/auto_lender.py`、`pulse/llm/jev.py`、`proxy/sticky_select.go`、`CONTEXT.md`

## 背景

Key Loan 的出借账号原本由管理员在额度看板手工指定，发放后固定不变。团队希望改成「按打分自动选号」：优先消化月底会被重置的额度，同时不侵占账号主负责人的用量。可选方案有两类：把决策交给一个 LLM，或继续用现有的确定性打分引擎。

## 决策

1. **硬过滤与算法分永远权威，Jev 只做重排。**
   耗尽、覆盖时长不足、名额已满、主负责人保留量不足一律在 Python 侧先行排除（`burn_rate._hard_filter_reason`）。Jev 只在存活候选（默认 Top-8）上排序，且必须通过护栏（置信度、首选与次优概率间隔、主负责人安全性、返回值必须落在候选集内）；任一不通过即回落算法首选。

2. **Jev 走 OpenRouter 的 Decisions 端点，不是 chat/completions。**
   Jev（`typesafe/jev-1.13`）是 System One 决策模型，输入应用状态加定型问题，返回带概率的定型答案。它不出现在 `GET /api/v1/models`，也不能发到 `/chat/completions`。本仓库用 `choice` 问「该借哪个账号」、用 `noul` 逐候选问「是否会侵占主负责人预留」。

3. **Jev 不进请求链路。**
   它跑在 web 侧「计算有序凭证列表」这一既有接缝（`/api/internal/v1/proxy/pool`，Go 每 60s 拉取）以及借用发放 / 定期重评上。候选特征哈希 + TTL 缓存（默认 600s）防抖动并省调用；连续失败触发熔断，冷却期内直接走算法分。

4. **人工基础分与主负责人保留量都是账号级字段，两条路径共用。**
   `proxy_score_adjust`（人工分）加在算法综合分上；`proxy_reserve_pct`（保留量）参与硬过滤。此前人工分只影响 Credential Pool 顺序、不影响借用选号，本次统一。

5. **Switch dwell 分两层。**
   Python 侧 `loan_selection.min_switch_minutes`（默认 30 分钟）以 `KeyLoan.source_bound_at` 为基准，窗口内只降权（`recency_penalty`）不排除；Go 侧 `SessionBinding.StickySince` + `stickyMinDwell` 保证请求时也不会因桶耗尽而频繁换账号。只有 Go 能真正约束请求时刻的行为，因此两层都需要。

## 后果

- 算法分始终计算：既是保底，也是 UI 对照与回测基线。UI 同时展示算法分、人工分与 Jev 决策，便于判断该相信谁。
- Jev 明确选出别的账号时，重评不再要求「算法分增益」。算法分是保底而非否决权，否则等于废掉主判。
- 按池打分需要目标模型。模型未知时退化为 `unknown`，要求 auto 与 api 两个桶都还有 Snapshot Headroom，与 Go `snapshotQuotaOK` 一致。
- 每桶额度只有百分比、没有绝对额度，`pool_surplus_cents` 是按 `limit_cents` 折算的近似值。候选之间比较时是单调变换，不改变排序；绝对值仅供展示。
- Jev 的 `state` 按 OpenRouter 参考文档序列化为字符串。若上游改为接受对象，只需调整 `pulse/llm/jev.py::_encode_state`。
