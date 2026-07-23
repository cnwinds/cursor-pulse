# 借用账号选择算法优化：临期优先消化 · 2 人上限 · 并发防护

**日期：** 2026-07-21  
**状态：** 已批准并实施  
**范围：** Key 借用的出借账号选择（`burn_rate` 打分、`key_loans` 发放、额度看板推荐、到期自动回收）

## 背景

现有出借选择算法在 `pulse/tool_center/burn_rate.py:110-153`：

```
score = remaining_headroom_pct × (1 + min(days_until_reset/30, 1))
```

排除 `exhausted` 与"按号主自身消耗速率预计撑不到重置日"（`exhausts_before_reset`）的账号，自助借用取第一名（`key_loans.py:273-293`），发放前 `issue_loan_key` 再校验一次非耗尽（`key_loans.py:334-336`）。

对照需求存在四个缺口：

1. **单账号借用人数无上限**：选号不查在借人数，`issue_loan_key` 不校验，`key_loans` 表也无约束。要求：一个账号最多同时借给 2 人。
2. **订阅到期日 `renews_on` 完全未参与打分**：账号临近到期照样可能被选中，剩余额度随之作废浪费。
3. **多人并发借用会超选**：先查推荐再发放是 check-then-act，无事务无锁，两人同时借会选中同一"最优"账号且都成功。
4. 次要问题：打分只看额度百分比不看绝对余量；快照新鲜度不参与（3 天前的快照与 5 分钟前的同等竞争）。

**核心诉求：临近作废的账号优先借出去消化，避免额度过期浪费。**

## 目标

1. 单账号同时在借人数 ≤ 2（可配置），所有发放入口强制，并发下不突破。
2. 打分以"作废前消化"为主导因子：引入统一作废截止日 `deadline`，临期且富余多的账号排前。
3. 号主保护不弱化：沿用号主自身消耗预测，且只把"号主用不完的部分"计入可消化额度。
4. 借用者可见所借 key 的自动回收日期。

## 非目标

- 不改借用者准入规则（自助借需自有账号额度 warning/exhausted、一人一笔在借、名下账号须已绑 Key）。
- 不改额度快照同步机制与频率。
- 不引入按 key 精确计量（沿用 baseline 差值近似，Cursor 用量事件不区分 key）。
- 不做跨团队调配；不动 `AccessRequest` 试用申请分配逻辑。

## 决策记录

| 项 | 选择 |
|---|---|
| 临期账号策略 | 优先借出消化（用户核心诉求），而非规避 |
| 作废截止日定义 | `deadline = min(快照 cycle_end, 账号 renews_on)`，先到者为准 |
| 人数上限执行点 | `issue_loan_key` 事务内复查（所有入口的唯一漏斗） |
| 并发机制 | sqlite：`BEGIN IMMEDIATE`；Postgres：对账号行 `SELECT ... FOR UPDATE` |
| 快照新鲜度 | 软惩罚入分，不硬过滤（避免全员快照偏旧时无号可借） |
| 打分形态 | 多因子 min-max 归一后加权和，权重入 `config.yaml` |

## 设计

### 1. 作废截止日（deadline）

额度在两个时点之一作废：账期重置（快照 `cycle_end`，Cursor 额度月底清零）或订阅到期不续（账号 `renews_on`）。统一为：

```
deadline = min(snapshot.cycle_end, account.renews_on)   # renews_on 为 NULL 时只取 cycle_end
days_to_deadline = (deadline - today).days
hours_to_deadline = 距 deadline 当天 23:59:59（UTC）的小时数   # 小时精度，驱动过滤与 urgency
```

key 的自动回收点同样对齐 deadline（见 §5）。

### 2. 硬过滤（不满足直接排除，不参与打分）

候选账号须全部满足：

- Cursor 账号、状态活跃、有额度快照（沿用现有）。
- `analyze_burn_rate` 非 `exhausted` 且非 `exhausts_before_reset`（沿用，号主保护）。
- **在借 active loans 数 < `max_active_loans_per_account`（默认 2）。**
- **`hours_to_deadline ≤ min_coverage_hours`（默认 1 小时）**：距作废不足 1 小时的账号不外借（key 即将回收，无意义）；已过期（deadline 已过）的账号同理由此排除。**大于 1 小时即可借，且时间越短打分权重越高（见 §3 urgency）。**

### 3. 打分公式：urgency 主导

核心思路：借用者应烧掉的是**号主自己用到作废也用不完的那部分**额度。口径与 `projected_exhaustion_date` 一致：

```
owner_daily_burn_cents = snapshot.used_cents / max(elapsed_days, 1)
days_precise = hours_to_deadline / 24                     # 可为小数（小时精度）
surplus_cents = max(snapshot.remaining_cents - owner_daily_burn_cents × days_precise, 0)
urgency = surplus_cents / max(days_precise, 1/24)         # 单位时间待消化额度；时间越短权重越大，除数下限 1 小时
```

`surplus_cents` 无法由 cents 推算时（`remaining_cents` 缺失），回退百分比路径：`daily_pct = total_pct / max(elapsed_days, 1)`，`surplus_pct = max(100 - (total_pct + daily_pct × days_precise), 0)`，再乘 `limit_cents/100` 得 `surplus_cents`；两者皆缺时按 0 处理（仍可被借，自然排后）。

U、S 两个额度因子在候选池内 min-max 归一到 [0,1]（单候选或全池相等时该项取 1）；L、F 本身即 [0,1] 绝对值，直接入分（对 F 做 min-max 会把快照时间的微差噪声放大成满分差距）：

| 因子 | 含义 | 默认权重 |
|---|---|---|
| U | `urgency`：日均待消化额度，临期+富余多 → 大 | 0.50 |
| S | `surplus_cents`：富余池绝对大小，借用者可用空间大 | 0.25 |
| L | `1 - active_loans / max_active_loans_per_account`：优先 0 人借用的账号，摊薄负载 | 0.15 |
| F | 快照新鲜度：`max(0, 1 - age_hours / freshness_full_penalty_hours)`（默认 24h） | 0.10 |

```
score = 0.50·U + 0.25·S + 0.15·L + 0.10·F
```

按 score 降序；打平按 `account_id` 字典序，保证确定性。

**示例**：甲剩 $180、30 天后重置、号主日耗 $2 → surplus $120、urgency $4/天；乙剩 $60、3 天后重置、号主日耗 $1 → surplus $57、urgency $19/天 → 乙排第一，先被消化。丙剩 $200 但已有 2 人在借 → 硬过滤排除。

### 4. 人数上限与并发防护

- 配置项 `tool_center.loan_selection.max_active_loans_per_account`（默认 2，≥1），过滤与发放校验共用。
- `issue_loan_key()` 是所有发放路径（自助借、看板管理员分配）的唯一漏斗：在**同一事务内** `COUNT(status='active' AND source_account_id=?)`，≥ 上限则抛 `KeyLoanError("该账号借用名额已满，请选择其他账号")`，然后才调用 Cursor `create_user_api_key`。调用方不得在 recommend 与 issue 之间提交事务（现状如此）。
- 并发机制（`_lock_account_for_loan_issue`，COUNT 之前调用）：Postgres 对 `ai_accounts` 行 `SELECT ... FOR UPDATE`；sqlite 在锁点执行一次 no-op self-UPDATE 抢占写锁（依赖 pysqlite legacy 模式下先前 SELECT 不持读快照、首个写操作才升级锁的驱动行为），并发写者在 busy timeout 内排队，后到者拿锁后 COUNT 必然看到先到者已占名额；超时未获锁转为 `KeyLoanError("系统繁忙，请稍后重试")`。锁持有至事务提交（覆盖后续远端 API 调用）。
- 同一借用人并发自助申请由 `_lock_member_for_self_loan` 串行化（同机制锁 `members` 行），锁后复查"借用者仅一笔在借"的既有规则；管理员手动分配路径不加此锁。ops 层 `KeyLoanError` 分支 rollback 以及时释放锁。
- 由此，两人同时抢同一账号：一成功一收到"名额已满"；同一借用人重复提交：一成功一收到"已有进行中的借用"。

### 5. 到期自动回收

- `expire_loans_on_reset`（`key_loans.py:193`，定时任务 `scheduler.py:366` 调用）判定日期由 `usage_resets_on` 扩展为 deadline：`min(usage_resets_on, renews_on) ≤ today` 即回收并标记 `expired`。函数名与签名保持不变，仅扩展条件。（`usage_resets_on` 与打分所用的快照 `cycle_end` 同源——同步时由 Cursor `billingCycleEnd` 写入，两处口径一致。）
- 远端撤销失败不阻断本地过期：`renews_on` 已过时账号可能已死，`revoke_user_api_key` 失败仅记 warning 并降级为纯本地清理（credential 置 revoked、本地标记 `expired`），避免 loan 永久卡 active 锁死借用者。残余风险：若账号事后恢复，Cursor 侧的 key 可能仍然存活（有 warning 日志留痕，可用性优先的有意取舍）。
- 发放结果与 `loan_payload` 增加 `loan_expires_on`（= deadline）字段；bot「我的借用」与看板借用列表展示，让借用者预期 key 何时回收。

### 6. 改动清单

| 文件 | 改动 |
|---|---|
| `pulse/config.py` | 新增 `LoanSelectionConfig`（上限/覆盖天数/新鲜度尺度/四权重）与 `ToolCenterConfig.loan_selection`，挂到 `AppConfig.tool_center` |
| `pulse/tool_center/burn_rate.py` | 新增 `LenderCandidate` dataclass（snapshot + account_id + identifier + renews_on + active_loan_count）；新排序函数按 §2 过滤、§3 打分（保持纯函数、可单测）；替换旧 `recommend_lenders`/`_lender_score` |
| `pulse/tool_center/key_loans.py` | 新增 `build_lender_candidates(session, team_id, exclude_account_ids=...)`：一次 GROUP BY 查各账号在借人数并组装候选；`recommend_lender_for_borrower` 改走它；`issue_loan_key` 加事务内上限复查与并发锁；`expire_loans_on_reset` 扩展 deadline；payload 加 `loan_expires_on` |
| `pulse/web/quota_api.py` | `/api/v2/quota-board/recommend` 改走 `build_lender_candidates`，看板排名与实际出借一致 |
| `pulse/tool_center/key_loan_ops.py` 及 bot 文案 | 借用结果/「我的借用」展示 `loan_expires_on`（如需） |
| `config.example.yaml` | 增 `tool_center.loan_selection` 配置块 |
| `pulse/channels/reminders/scheduler.py` | 回收任务无需改接线（函数内逻辑扩展），确认调用参数不变 |

### 7. 配置

```yaml
tool_center:
  loan_selection:
    max_active_loans_per_account: 2
    min_coverage_hours: 1
    freshness_full_penalty_hours: 24
    weight_urgency: 0.50
    weight_surplus: 0.25
    weight_load: 0.15
    weight_freshness: 0.10
```

### 8. 边界情况

- `renews_on` 为 NULL（手工字段，可能未填）→ deadline 只取 `cycle_end`。
- deadline 已过（重置日或订阅到期日早于现在）→ `hours_to_deadline = 0 ≤ min_coverage_hours`，硬过滤排除。
- `min_coverage_hours` 配置为 0 且 deadline 为当天 → urgency 除数取 `max(days_precise, 1/24)` 下限防除零/数值爆炸。
- 快照过旧：不硬过滤，F 因子趋 0 自然排后；全部候选被过滤时返回"当前没有可借出的富余账号"（沿用现有报错）。
- `remaining_cents`/`total_pct` 均缺失的快照 → surplus 按 0，账号仍可借但排最后。
- 自助借用排除借用者自有账号的逻辑不变；候选组装时以 `exclude_account_ids` 传入。

### 9. 测试计划

打分（`tests/test_burn_rate.py` 重写/新增）：

- 临期高 urgency 账号胜过远期高剩余账号（§3 示例的甲乙场景）。
- 在借 2 人的账号被硬过滤；在借 1 人的账号 L 因子低于 0 人账号。
- `hours_to_deadline ≤ min_coverage_hours` 被过滤；`renews_on` 早于 `cycle_end` 时 deadline 取 `renews_on`；相同富余下距作废时间更短的账号 urgency 显著更大。
- surplus 计算：号主自身将耗尽时 surplus=0；cents 缺失时走百分比回退。
- 归一：单候选各项=1；权重配置覆盖生效。
- 排序确定性：同分按 account_id。

发放与上限（新增 `tests/test_key_loan_caps.py`）：

- 账号已有 1 笔在借 → 发放成功；已有 2 笔 → `KeyLoanError("该账号借用名额已满")`。
- 上限配置改为 1 时，已有 1 笔即拒绝。
- 自助借路径：推荐结果不含满员账号。
- 同事务复查：mock 在 recommend 后注入一笔在借，issue 应拒绝（验证 check 在事务内而非依赖推荐快照）。

回收与展示：

- `renews_on` 早于 `usage_resets_on` 且已过当天 → 借用被回收并标记 `expired`（沿用现有 reset 回收用例结构）。
- `loan_payload` 含 `loan_expires_on` 且等于 deadline。

回归：`test_key_loan_dingtalk.py`、`test_quota_api.py`、`test_sync_quota.py` 等既有测试按签名变化同步更新，保持全绿。

### 10. 验收标准

1. 构造甲（30 天后重置、surplus $120）乙（3 天后重置、surplus $57）两账号，推荐列表乙在甲前。
2. 某账号已有 2 笔 active loan：不推荐；管理员直接对该账号发放也被拒绝。
3. 两个并发发放请求指向同一仅剩 1 名额的账号：恰好一个成功，另一个收到名额已满。
4. 账号 `renews_on` 早于账期重置日时，借用在 `renews_on` 当天被自动回收。
5. 既有测试套件全绿。
