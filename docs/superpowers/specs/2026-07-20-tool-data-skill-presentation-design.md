# Tool 出数 / Skill 定版式（LLM 渲染）设计

**日期：** 2026-07-20  
**状态：** 已落地（成功路径禁止 user_message；失败保留；过渡期部分能力 `result.text`）  
**范围：** 全部 Capability Tool；Skill 展示版式；Agent 排版  
**展示策略：** **方案 B** — 由 LLM 按 Skill 文档排版  
**前置：** [2026-07-20-dingtalk-skills-tools-design.md](./2026-07-20-dingtalk-skills-tools-design.md)  
**计划：** [2026-07-20-all-skills-no-user-message.md](../plans/2026-07-20-all-skills-no-user-message.md)

## 1. 目标

1. **Tool = 数据**：成功时以结构化 `result`（含 `schema_version`）为唯一数据源；**`user_message` 必须为空字符串**。  
2. **Skill = 版式说明书**：任务分节写「展示版式」。  
3. **Agent = 渲染者**：按 Skill 排版；禁止编造；禁止依赖成功路径的 `user_message`。  
4. **失败例外**：`status=failed` 仍可用 `error_code` + `user_message`。  
5. **无 verbatim**：私聊也不再原文直出 tool `user_message`；Key 放在 `result` 中，私聊 policy 要求完整展示。

## 2. 非目标

- 服务端 Skill 模板引擎 / Jinja（方案 A）  
- Skill↔Tool 系统级外键  
- Channel 口令路径的字符串 formatter（仍可服务非 Agent 入口）

## 3. 协议

### 3.1 CapabilityInvokeResult

成功时：

| 字段 | 要求 |
|------|------|
| `status` | `succeeded` |
| `result` | **必填**，含 `schema_version`；过渡期可含 `text`（原 Markdown） |
| `user_message` | **必须为 `""`** |

失败时：`error_code` + `user_message`。

Agent 回传 LLM：

```json
{
  "ok": true,
  "status": "succeeded",
  "user_message": "",
  "result": { "schema_version": 1, "...": "..." }
}
```

### 3.2 `usage.self.read` result schema（v1）

```json
{
  "schema_version": 1,
  "query": {
    "mode": "billing_cycle | calendar_month",
    "period": "YYYY-MM"
  },
  "accounts": [
    {
      "kind": "owned | loan",
      "identifier": "email-or-label",
      "window_label": "string",
      "range_text": "string",
      "events": 0,
      "tokens": 0,
      "cost_usd": 0.0,
      "data_updated_at": "iso-or-null",
      "models": [
        { "model": "string", "events": 0, "tokens": 0, "cost_usd": 0.0 }
      ],
      "loan": {
        "lender_name": "string",
        "loan_created_at": "iso-or-null",
        "borrowed_quota_pct": null,
        "remaining_headroom_pct": null
      }
    }
  ],
  "empty_reason": null
}
```

无账号且无借用时：`accounts: []`，`empty_reason: "no_cursor_or_loan"`。

### 3.3 Skill 文档约定（版式分节）

在 `cursor.self/tasks/my-usage.md` 增加 **「展示版式」** 分节，至少包含：

1. 调用 `usage_self_read` 后，**只使用** `result` 中的数字与字段。
2. 标题：记账周期 vs 自然月。
3. 总览表列：账号 / 次数 / Tokens / 费用（可合计行）。
4. 每账号：周期、主力模型（按 tokens 或费用占比 Top N）、思考模型（名称含 thinking 的 Top N，可选）。
5. `kind=loan`：借出人、起始日、已消耗 %、还能用 %。
6. 空数据：使用 `empty_reason` 对应固定话术（文档写死一句）。
7. **禁止**：编造未出现在 `result` 的费用/次数；不得省略账号。

Agent policy 增加规则：当 tool 返回带 `schema_version` 的 `result` 时，须先 `load_skill_docs`（若尚未加载对应分节），再按文档排版；`user_message` 仅作参考。

## 4. 试点实现要点


| 组件                        | 改动                                                                                                                    |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `usage_self.py`           | 抽出 `build_usage_self_payload(...)` → dict；`build_usage_self_reply` 改为 payload + `format_usage_self_message`（fallback） |
| `usage_self_read` handler | `result=payload`；`user_message` 仍可用旧 formatter 填充（兼容非 Agent 路径）                                                       |
| `my-usage.md`             | 补「展示版式」分节                                                                                                             |
| `agent_policy.py`         | 增加「有结构化 result 时按 Skill 排版」规则                                                                                         |
| 测试                        | payload 结构断言；formatter 回归保留；可选：文档含版式关键词                                                                               |


## 5. 成功标准

1. 私聊「我的用量」仍能给出分账号总览（数字与现网一致量级）。
2. Tool 的 `result.accounts` 可被单测断言，不依赖 Markdown 字符串。
3. Skill 文档明确版式；Agent 有 policy 约束。
4. 借 Key 等 verbatim 能力行为不变。

## 6. 风险


| 风险                    | 缓解                                            |
| --------------------- | --------------------------------------------- |
| LLM 排版漂移              | 版式分节写死列名；policy 禁编造                           |
| 未 load_skill_docs 就渲染 | policy：缺文档时先 load，或退回 `user_message` fallback |
| 双轨文案不一致               | 试点期 fallback 与文档对齐；后续再删 handler 长文案           |


## 7. 后续（非本试点）

- 推广到 `quota.self.read`、`key.loan.self.read` 等读类能力  
- 评估是否对高稳表格改回方案 A（服务端模板）

---

**变更记录**


| 日期         | 说明                                                  |
| ---------- | --------------------------------------------------- |
| 2026-07-20 | 初稿：方案 B + usage.self.read 试点协议                      |
| 2026-07-20 | 试点落地：payload / handler / my-usage 版式 / agent_policy |


