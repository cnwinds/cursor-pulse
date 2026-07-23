# 钉钉对话改为 LLM Agent + Tools

**日期：** 2026-07-16  
**状态：** 已批准并实施  
**范围：** Assistant 文本编排主路径；钉钉 Channel 收发边界不变；为按用户隔离与后续个人记忆检索预留契约

## 背景

当前钉钉文本命令已 cutover 到 Assistant Platform，编排顺序为：

1. 系统级 pending 确认（「确认/取消」）
2. 规则意图 `match_capability_intent`（`intents.py`）
3. 失败或未命中时 LLM JSON 意图分类（`llm_intent.py`）
4. `CapabilityExecutor` → Pulse handlers
5. 记忆回复 / `simple_reply` 兜底

问题：规则与 LLM 分类双轨并存，自然语言体验不稳定；固定口令与帮助文案维护成本高；已有 `catalog.input_schema` 与 Pulse 侧 `complete_with_tools`，但对话路径未统一成 tool-calling。

## 目标

1. **钉钉文本主路径改为 LLM Agent**：模型按需调用 tools，汇总后回复用户；不再做命令模式匹配与 JSON 意图分类。
2. **能力即工具**：角色过滤后的 capability catalog 映射为 OpenAI tools；执行仍走 `CapabilityExecutor`（权限、审计、Pulse 调用保留）。
3. **会话历史**：带上当前 open session 历史；与现有会话 idle 自动关闭对齐。
4. **按用户隔离**：不同用户的对话上下文严格隔离，并为「每用户记忆文件 + 可检索历史」预留稳定身份键与工具扩展点。
5. **Prompt Studio 继续生效**：session pin 的 `heart.md` / `precepts.md` 注入 Agent system。

## 非目标

- 不改钉钉 Stream / mirror / `reply.send` 收发链路
- 不改 Channel 本地路径：工作群「启动」、引导图状态机、CSV/截图上传
- 本期不实现完整「每用户记忆文件」产品（只定隔离契约与扩展点）
- 不把 Web 管理端 `planner.py` 与钉钉 Agent 强行合并为同一 runtime
- 不引入系统级 pending 确认（确认由模型在多轮对话中完成）

## 决策记录

| 项 | 选择 |
|---|---|
| 固定短命令 | 全部取消模式匹配，一律走 LLM + tools |
| 敏感操作确认 | 模型口头确认后再调 tool（不做系统 pending 拦截） |
| Tool 循环 | 最多 N 轮（默认 20，可配置）；每轮可并行多个 tool_calls |
| 架构落点 | Orchestrator 内嵌 `AgentRuntime`（方案 1），复用 Executor |
| 帮助 | 不下发 `bot.help` tool；模型根据当前 tools 列表自述 |
| 跨轮历史 | 加载当前 open session 的 user/assistant 文本 |
| Prompt | `compose_system_supplement(session.prompt_release_id)` 必须注入 |
| LLM 不可用 | 固定降级文案，不回退规则匹配 |

## 架构

### 消息流（文本）

```
钉钉 Stream
  → DingTalkChannelHandler（群未 @ 丢弃；本地特殊路径除外）
  → mirror → Assistant EventIngest → session.process
  → generate_reply_text
       → AgentRuntime.run(
            user_text,
            tools=capabilities_as_tools(role),
            messages=session_history + current_user,
            system=agent_policy + prompt_studio_fragments
          )
       → 循环 complete_with_tools → 并行执行 tools → 回填
       → 最终自然语言
  → 写入 assistant ChatMessage → reply.send → 钉钉
```

### 组件职责

| 组件 | 职责 |
|------|------|
| `AgentRuntime`（新） | messages 组装、tool 循环、并行执行、轮数/超时控制 |
| `tools_from_capabilities`（新） | `ResolvedCapability` → OpenAI function tools |
| `AssistantLlmClient`（增强） | `complete_with_tools(messages, tools)`，支持 tool_call_id |
| `CapabilityExecutor`（保留） | 鉴权、调用 Pulse、redact、审计 |
| `generate_reply_text`（改写） | 主路径只调 Agent；失败再 memory / simple_reply |
| Channel 本地（不变） | 启动、引导图、文件/CSV |

### 下线（对话主路径不再调用）

- `match_capability_intent` / `intents.py` 规则表
- `classify_intent` / `extract_arguments` / `assist_unrecognized_command`
- 系统 `pending` 确认流（`_handle_pending_confirmation` 等）
- 将 `bot.help` 排除出对话 tools（catalog 可保留供文档/能力中心，但不进 Agent tools）

第一期可保留源文件以免大幅删测；主路径解绑后，测试改完再物理删除。

## Tools 映射

### 规则

- 输入：`resolve_capabilities(team_id, role, member_id)` 的结果
- 排除：`bot.help`
- `capability_key` → `function.name`：`.` 替换为 `_`（如 `quota.self.read` → `quota_self_read`）；执行时再反查回 key
- `display_name` + `description` → `function.description`
- `input_schema` → `function.parameters`

### 执行

- 每个 tool call → `CapabilityExecutor.invoke(..., confirmed=True, arguments=parsed_json)`
- 同轮多个 tool_calls **并行**执行；单个失败以结构化 error 回填，不影响同轮其它调用
- 未授权 / 未知 name：执行层拒绝并回填原因（即使模型幻觉出名字）

### 特殊能力

- **`usage.query`**：保留。Prompt 约定：简单本人用量/额度优先 `usage.self.read` / `quota.self.read`；复杂分析再用 `usage.query`，避免无意义双层 LLM。
- **帮助**：无专用 tool；system 要求根据当前 tools 总结用法。

## Agent 循环

```
max_rounds = config.agent_max_tool_rounds  # default 20

messages = [system, ...session_history..., current_user]

for round in 1..max_rounds:
    resp = llm.complete_with_tools(messages, tools)
    if not resp.tool_calls:
        return resp.content or fallback
    results = parallel_invoke(resp.tool_calls)
    append assistant(tool_calls) + tool(role) messages
return "步骤较多，请拆成更小的请求"  # 达上限
```

### 可配置项

| 键 | 默认 | 含义 |
|----|------|------|
| `agent_max_tool_rounds` | 20 | 单次用户消息内 tool 轮数上限 |
| `agent_total_timeout_seconds` | 实现时定 | 整次 Agent 墙钟超时 |
| `agent_history_max_messages` | 可选 | 单 session 装入模型的最大消息条数（额外保险） |

单 tool 超时沿用 catalog `timeout_seconds`。

### System 组成

1. **Agent 固定策略**（代码内模板）：如何用 tools、并行、口头确认敏感操作、帮助自述 tools、勿编造未授权能力、勿泄露密钥。
2. **Prompt Studio 片段**：`compose_system_supplement(db, session.prompt_release_id)`（`heart.md` + `precepts.md`）。
3. （可选）运行时注入「当前可用能力 display_name 列表」摘要，降低模型漏用工具概率。

上线后建议在 Prompt Studio 发新版 precepts：从「固定命令格式」改为「tool / display_name / 先确认再执行」。

## 会话历史

### 装载

- 仅当前 **open** `ChatSession` 内 `role in (user, assistant)` 的 `text_redacted`
- **不**把中间 tool JSON 持久化进 `ap_chat_messages`（过长、易含敏感字段）
- 本轮 Agent 循环内的 tool 轨迹只存在内存 messages 中
- 最终 assistant 文本写入 session，供下一轮用户消息使用

### 与会话关闭的关系

- 私聊 idle 默认 30 分钟、群聊 10 分钟（现有逻辑）关闭 session
- 关闭后下一条消息开新 session → **空历史**，自然清空上下文
- 不另造产品级「历史窗口」概念；session 边界即上下文边界

## 用户隔离与后续记忆（硬性契约）

### 现状（必须保持）

私聊 session 键：`(assistant_id, team_id, channel, conversation_type=private, user_id)`，其中 `user_id = sender_channel_user_id`。  
群聊按 `conversation_id`（群）隔离，不与私聊历史混用。

Agent 装载历史时 **只允许** 读取：

- 当前 `session_row.id` 下的消息；或
- 明确按 **同一 `user_id` + team/channel** 查询的数据源

禁止：跨 `user_id` 拼接 messages；禁止把用户 A 的 tool 结果/记忆注入用户 B 的请求。

### 身份锚点（为记忆预留）

后续「每用户记忆文件」与「可搜索该用户聊天历史」必须以稳定主体 ID 为键。约定：

| 字段 | 用途 |
|------|------|
| `team_id` | 租户/命名空间 |
| `channel` | 渠道（如 dingtalk） |
| `subject_id` | 优先 `member_id`（台账成员）；若无则 `sender_channel_user_id` |
| `session_id` | 单次连续对话；记忆蒸馏可跨 session，但检索必须带 `subject_id` |

与现有 `personamem` 的 `VisibilityContext.private(user_id)` / `subject_id` 对齐；Agent 路径写入或检索记忆时必须传入同一 `subject_id`，不得用群 `conversation_id` 冒充私聊用户。

### 本期不做、但接口预留

1. **记忆检索 tool（二期候选）**  
   例如 `memory.search` / `memory.read_profile`：参数含 query；实现内强制 `subject_id=当前用户`，无「查他人记忆」参数。
2. **每用户记忆文件**  
   文件或存储对象路径/主键包含 `team_id + subject_id`；会话关闭蒸馏（现有 `close_and_distill`）写入该用户命名空间。
3. **历史搜索范围**  
   默认可搜「该 subject 在本 team 下的历史会话摘要/记忆」，不是当前 open session 全文 dump；open session 仍靠 messages 装载。

### 群聊注意

群内多人 @ 机器人时，session 按群隔离；若回复依赖个人记忆，必须用 **发言者** `sender` 的 `subject_id` 检索私有记忆，且不得把检索结果写成「群共享记忆」除非产品明确要求。

## 确认策略

- System prompt：对 catalog 中 `risk_level` 为 `sensitive` / `destructive`（及 `confirmation_required`）的操作，先说明将执行的内容，等用户明确同意后再发起对应 tool call。
- 执行层：`confirmed=True`；权限与审计不变。
- 风险：模型可能跳过确认直接调用 → 靠 prompt + 审计日志；二期若不够再加系统硬拦截（非本期范围）。

## 错误与降级

| 情况 | 行为 |
|------|------|
| LLM 未配置或调用失败 | 固定「助手暂时不可用」；不回退规则匹配 |
| tool 参数非法 | 校验错误回填模型，由其改参或询问用户 |
| 无权 / 未知 tool | 回填拒绝原因 |
| 达 tool 轮数上限 | 提示用户拆分请求 |
| Agent 失败且无可用回复 | 可选 `try_memory_reply`（仍按当前用户 visibility）→ `simple_reply` |

## 测试要点

1. catalog → tools 映射与 name 往返；`bot.help` 不出现在 tools。
2. Agent：无 tool 直出；单 tool；同轮并行多 tool；达 round 上限。
3. 历史：只装载当前 open session；idle 关闭后新 session 无历史。
4. **隔离**：用户 A 的 mock 历史不得出现在用户 B 的 messages；记忆/检索 mock 校验 `subject_id`。
5. 集成：mock LLM tool_calls → Executor 调用参数正确 → 最终回复写入并 reply。
6. 回归：原固定命令用例改为「mock 模型选择正确 tool」；多轮「先确认再执行」fixture。
7. Prompt：有 `prompt_release_id` 时 system 含 heart/precepts 内容。

## 落地顺序

1. `AssistantLlmClient.complete_with_tools` + tools 映射 + `AgentRuntime`（单测）
2. 接线 `generate_reply_text`（可用 feature flag 回滚）
3. 解绑 intents / llm_intent / pending；更新测试与 `docs/bot-commands.md`
4. Prompt Studio 发适配 tools 的 precepts 新版本
5. 观察后再删除死代码
6. （二期）`memory.search` tool + 每用户记忆文件

## 风险摘要

| 风险 | 缓解 |
|------|------|
| 每条消息都调 LLM，成本/延迟上升 | 已接受；靠轮数上限与超时 |
| 模型跳过口头确认 | prompt + 审计；二期可硬拦 |
| 帮助文案不稳定 | tool description 写清楚；抽检 |
| `usage.query` 双层 LLM | prompt 分流 + 监控 |
| 用户上下文串台 | session/subject 键强制；单测隔离；记忆 API 无跨用户参数 |

## 参考代码锚点

- 编排：`assistant_platform/conversation/orchestrator.py`
- 目录：`assistant_platform/capabilities/catalog.py`
- 执行：`assistant_platform/capabilities/executor.py`
- Prompt：`assistant_platform/prompts/compose.py`
- 会话键：`assistant_platform/conversation/session_store.py`
- 记忆可见性：`assistant_platform/memory/wiring.py`
- Tools 参考：`pulse/llm/client.py::complete_with_tools`、`pulse/chat/planner.py`
