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
   它跑在 web 侧「计算有序凭证列表」这一既有接缝（`/api/internal/v1/proxy/pool`，Go 每 60s 拉取）以及借用发放上。候选特征哈希 + TTL 缓存（默认 600s）防抖动并省调用；连续失败触发熔断，冷却期内直接走算法分。自动分配借用在请求路径上要用的候选白名单（`pool_board.loan_candidate_credentials`）只按确定性打分排序，不问 Jev。

   每次「有决策价值」的决策（Jev 选中了账号，或出现回落原因）都会写一条 `lender_auto_pick` 审计事件，含来源、置信度、回落原因与选中账号。池轮询与预览不写，避免事件表被 60s 轮询刷爆。

4. **人工基础分与主负责人保留量都是账号级字段，两条路径共用。**
   `proxy_score_adjust`（人工分）加在算法综合分上；`proxy_reserve_pct`（保留量）参与硬过滤。此前人工分只影响 Credential Pool 顺序、不影响借用选号，本次统一。

5. **两套借用机制并存，由 `KeyLoan.lender_mode` 区分。**
   - **指定借用（`manual`）**：发放时在选定账号上建一把独立 Cursor key（`key_role=loan`），借用 Key 固定绑定它；授权不下发候选白名单，Go 走 `passthroughToken`。管理员可用 `reassign_loan_source` 手动改绑。
   - **自动分配借用（`auto`）**：授权下发按分数排序的候选**主凭证**白名单（`pool_board.loan_candidate_credentials`），Go 在 `loan_alias` 上按 Credential Pool 的方式选号——per-session sticky + Switch Dwell + 按 Quota Pool。换号不新建 Cursor key。

6. **换号由代理在会话内完成，不在 DB 层做。**
   早期实现是「定时任务 + `reassign_loan_source`」，每次换号都要在 Cursor 侧新建一把 Key 并吊销旧 Key，且粒度只能是分钟级，做不到「使用过程中灵活更换」。该路径已退休（`reevaluate_auto_loans` 与 `auto_lender_reevaluate` 作业移除）；`reassign_loan_source` 保留为管理员手动改绑。

7. **Switch dwell 分两层，作用不同。**
   评分侧 `loan_selection.min_switch_minutes` 只影响降权（`recency_penalty`）；请求侧由 Go `SessionBinding.StickySince` + `stickyMinDwell` 保证同一会话在窗口内不因桶耗尽换账号。自动分配借用真正生效的是后者。

8. **管理员「自动分配」改为账号池路由（`routing_mode=pool`）。**
   发放的 pka_ 不选定起始账号，也不新建 Cursor Key。授权返回 `mode=loan_pool`，Go 与历史 `pk_` 共用同一 Credential Pool（入池账号、打分表、sticky）。指定账号仍是固定借用。自助借 Key 仍走上面的候选白名单，不改成整池。`pk_` 发放留在「账号池 → 历史接入密钥」；新成员走借用记录的自动分配。入池开关和打分表保留。

9. **同一账号的同时在线人数有上限。**
   默认 3（`loan_selection.max_concurrent_users`，0 为不限制）。Go 在换票、会话续期、以及 sticky 因额度耗尽换号时上报 `current_credential_id`；`release_current` 表示正在离开该凭证。Web 按成员计座（否则按借用单或接入密钥）：同一成员在同一账号上只占一席，同时使用两个账号则各占一席。超时（默认 180 秒）后座位失效。已在座的人不被后来者挤走。指定借用占座但不会因为满员被拒绝。顾问调用失败时 Go 按本地顺序选号并跳过上次的满员名单；Web 明确返回空分配时不再把人放进已满账号。没有候选且不是正在离开时不算人数上限，代理按池空处理。座位在单个 Web 进程的内存里。整套规则和可调参数在「系统设置 → 选号规则」，团队覆盖经 `effective_config` 生效。池轮询只读已保存的团队配置，不在这条路径上创建团队或回填成员。

## 后果

- 算法分始终计算：既是保底，也是 UI 对照与回测基线。UI 同时展示算法分、人工分与 Jev 决策，便于判断该相信谁。
- Jev 明确选出别的账号时，不再要求「算法分增益」。算法分是保底而非否决权，否则等于废掉主判。
- 按池打分需要目标模型。借用选号在模型未知时退化为 `unknown`，要求 auto 与 api 两个桶都还有 Snapshot Headroom，与 Go `snapshotQuotaOK` 一致；代理入池不适用该退化，仍走 CONTEXT.md 的 Credential Pool Intake「任一桶有余量即可入池」。
- **自动分配借用消耗候选账号的 primary 凭证**，与共享池共用同一批凭证：好处是不再为换号产生临时 Key，代价是 Cursor 侧不再按 Key 区分「借出去的」与「池里的」，只能靠 Pulse 的 `loan_id` 归因。因此自动分配的借用消耗以代理账本按 `loan_id` 汇总为准（`borrowed_basis=proxy`），不再用单账号快照差值。
- **`KeyLoan.source_account_id` 语义变化**：自动分配借用下它是「起始 / 当前偏好账号」，不是唯一来源。UI 显示的借出账号可能与实际服务账号不同，实际账号看借用用量明细。
- 每桶额度只有百分比、没有绝对额度，`pool_surplus_cents` 是按 `limit_cents` 折算的近似值。候选之间比较时是单调变换，不改变排序；绝对值仅供展示。
- Jev 的 `state` 按 OpenRouter 参考文档序列化为字符串。若上游改为接受对象，只需调整 `pulse/llm/jev.py::_encode_state`。
