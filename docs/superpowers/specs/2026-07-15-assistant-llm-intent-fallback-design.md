# Assistant LLM 意图兜底（规则 miss → 分类 / 抽参）

**日期：** 2026-07-15  
**状态：** 已批准并实施  
**范围：** 关键字意图未命中时，由 Assistant 侧 LLM 做授权能力分类与按需抽参；不替代 Capability Executor，不把对话大脑迁回 Pulse

## 背景

当前钉钉主对话路径为：

1. `match_capability_intent`（关键字 / 正则）
2. 命中 → `CapabilityExecutor` → Pulse Provider
3. miss → 规则记忆闲聊 → `simple_reply`

关键字快、可控、可测，但同义改写易漏（例如「查看我借用的key」未命中「我借的」类规则），miss 后落入记忆模板，出现借/借出混淆与固定 CSV CTA。

平台设计已规定：对话编排与「调模型 / 授权工具规划」属于 Assistant；Pulse 为业务 Provider。现状 LLM 客户端与 Admin `planner.py` 仍在 Pulse，Assistant 无 LLM 配置——对话智能与编排进程错位。

## 目标

1. **保留关键字优先**：命中则不走 LLM。
2. **miss 且 Assistant LLM 开启时**：先意图分类，需要参数再抽参，再经 Executor 执行。
3. **安全**：只暴露已授权能力；低置信反问；敏感/破坏性能力强制确认；闲聊才进记忆。
4. **边界**：对话 LLM 落在 Assistant；Pulse 暂留域内 LLM（Vision、月报叙述等）。
5. **可关断**：`ASSISTANT_LLM_ENABLED=false` 时行为与今天完全一致。

## 非目标

- 完整 Prompt Runtime（P0–P7）与 Prompt 发布流水线
- 迁移 Admin `ChatService` / `planner.py`
- 将 Pulse Vision / 报告 / 知识整理 LLM 迁入 Assistant
- 用 LLM 覆盖或改写已命中的关键字意图
- 独立 LLM 微服务

## 决策记录

| 项 | 选择 |
|---|---|
| miss 后 AI 职责 | 两步：先分类；需参再抽参（方案 C） |
| 低置信 / 闲聊 / 敏感 | 低置信反问；明确闲聊进记忆；sensitive/destructive 强制确认（方案 C） |
| LLM 放置 | 对话 LLM → Assistant；域内 LLM 暂留 Pulse |
| 实现路径 | Orchestrator 内规则 → LLM 分类 →（可选）抽参 → Executor（方案 1） |

---

## 1. 编排流与优先级

挂点：`assistant_platform/conversation/orchestrator.py` → `generate_reply_text`。

```text
1. match_capability_intent(text)
   └─ 命中 → CapabilityExecutor（与现网相同）
2. 若 Assistant LLM 已启用：
   a. 分类（仅已授权能力目录 + chat / clarify）
   b. capability 且需参数 → 抽参；缺必填 → 反问补参
   c. risk ∈ {sensitive, destructive} 或需确认 → 挂起确认态，先回确认文案
   d. 否则 → CapabilityExecutor（confirmed 按策略）
   e. decision=chat → try_memory_reply
   f. decision=clarify 或低置信 → 反问；不执行能力；不套 CSV 记忆模板
3. LLM 关闭或调用失败 → try_memory_reply → simple_reply
```

原则：

- 关键字永远优先
- LLM 只补 miss
- 执行只走 `CapabilityExecutor`（权限、审计、Provider 契约不变）
- LLM 失败降级，不阻断会话

---

## 2. 分类 / 抽参契约

### 2.1 分类器输入

- 用户原文
- `resolve_capabilities` 结果中的能力摘要：`key`、`display_name`、`description`、`risk_level`（**不含**未授权能力）
- 可选：最近 1～2 轮短上下文（脱敏，无密钥）

### 2.2 分类器输出

```json
{
  "decision": "capability" | "chat" | "clarify",
  "capability_key": "key.loan.self.read",
  "confidence": 0.0,
  "clarify_question": "你是想查当前借入的 Key，还是申请借用？",
  "needs_args": false
}
```

服务端强制规则：

- `confidence < ASSISTANT_LLM_INTENT_MIN_CONFIDENCE`（默认 `0.6`）→ 视为 `clarify`
- `capability_key` 不在授权集合 → 丢弃，降为 `clarify` 或 `chat`
- `needs_args` 以 catalog / `input_schema` 服务端校验为准；模型建议仅作参考
- 禁止执行模型编造的 key

### 2.3 抽参（第二步）

触发：分类为 capability，且该能力除透传 `text` 外仍有必填字段（例如 `cursor.key.bind` 的 `api_key`）。

- 输入：原文 + 该能力 `input_schema`
- 输出：`arguments`；缺必填 → 反问，不调用 Executor
- 禁止 schema 外字段

多数自助查询（额度、我的用量、我的借用）仅需 `{ "text": "<原文>" }`，可跳过抽参。

### 2.4 确认态

- `risk_level ∈ {sensitive, destructive}`：**即使高置信**，先回复将执行的能力说明，请用户回复「确认」或「取消」
- 状态挂在会话级 `pending_capability`（建议 TTL 5 分钟）：`capability_key`、`arguments`、`created_at`
- 下一句优先处理「确认」「取消」（关键字，不经 LLM）；确认后以 `confirmed=true` 调用 Executor
- 超时或取消：清除挂起，友好提示

### 2.5 与记忆

- 仅 `decision=chat` 进入 `try_memory_reply`
- `clarify` / 等待确认：**不得**使用「记得你提过…请发 CSV」类固定 CTA

---

## 3. 配置与客户端边界

### 3.1 Assistant 配置

| 变量 | 含义 | 默认 |
|------|------|------|
| `ASSISTANT_LLM_ENABLED` | 开启 miss 后分类/抽参 | `false` |
| `ASSISTANT_LLM_API_KEY` | API Key | 空 |
| `ASSISTANT_LLM_BASE_URL` | OpenAI 兼容 Base URL | 空 |
| `ASSISTANT_LLM_MODEL` | 模型名 | 空 |
| `ASSISTANT_LLM_INTENT_MIN_CONFIDENCE` | 低于则 clarify | `0.6` |

写入 `AssistantConfig`；进程与 Pulse 的 `LLM_*` **独立**（值可相同）。

### 3.2 客户端

- 新建 `assistant_platform/llm/`（精简：`complete` + JSON 结构化输出），或抽同仓无状态共享包
- **禁止** Assistant 通过 `import pulse.llm` 依赖 Pulse 业务包
- Pulse 继续用现有 `pulse/llm` 服务 Vision、报告等

### 3.3 审计

记录（无密钥明文）：分类决策、confidence、chosen key、是否 clarify/确认、错误码。失败打 warn 并降级。

---

## 4. 模块关系（本迭代）

```text
Channel Adapter (Pulse)
  → Assistant ingest / session job
    → generate_reply_text
         ├─ rules intent
         ├─ assistant_platform/llm intent classify (+ optional extract)
         ├─ CapabilityExecutor → Pulse Provider HTTP
         └─ memory / simple_reply
```

长期可演进为 Prompt Runtime 授权工具规划；本设计是过渡层，不阻塞后续替换挂点内部实现。

---

## 5. 测试与验收

### 单测建议

- LLM 关闭：编排路径与现网快照一致
- Mock 分类：同义「查看我借用的key」→ `key.loan.self.read`
- 非法 / 未授权 key → 不执行
- 低置信 → clarify 文案，不进记忆 CSV 模板
- sensitive 能力 → 进入 pending，确认后才 invoke
- 抽参缺字段 → 反问

### 验收清单

- [x] `ASSISTANT_LLM_ENABLED=false` 行为与现状一致
- [x] 开启后，授权用户同义借入查询可到达 `key.loan.self.read`（规则或 LLM）
- [x] 未授权能力不可被 LLM 选中执行
- [x] 低置信反问；闲聊才进记忆
- [x] sensitive/destructive 需确认轮
- [x] 关键字已命中路径回归通过
- [x] 相关单测通过

## 风险

- 延迟：每条规则 miss 多 1～2 次 LLM；异步 job 可接受，需监控超时
- 幻觉：靠授权集合过滤 + Executor 闸门；确认态降低误执行
- 半规则半模型：默认关闭 LLM，文档标明开启条件
- 与记忆顺序：clarify/确认轮避免先 `record_turn` 污染（实现时注意）
