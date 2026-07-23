# 小脉 Assistant Platform 系统设计

> 版本：v1  
> 日期：2026-07-14  
> 状态：Phase 0–5 已落地（master）  
> Phase 0 计划：[2026-07-14-assistant-platform-phase0.md](../plans/2026-07-14-assistant-platform-phase0.md)  
> Phase 1 计划：[2026-07-14-assistant-platform-phase1.md](../plans/2026-07-14-assistant-platform-phase1.md)  
> Phase 2–5 计划：[2026-07-14-assistant-platform-phases2-5.md](../plans/2026-07-14-assistant-platform-phases2-5.md)  
> 适用范围：Cursor Pulse 中的钉钉机器人与 Web Chat  
> 目标规模：单团队、10–50 人、单助手“小脉”，数据模型预留 `assistant_id`

## 1. 执行摘要

本项目不是继续为钉钉机器人增加命令，而是把“小脉”建设成一个可运营的数字员工：

- 开发者实现并注册真实能力。
- 管理员通过后台把能力加入团队、角色或指定用户，无需重新部署。
- 每次聊天、工具调用、确认、错误和回复都可追踪。
- 每个用户拥有隔离、可解释、可纠正的长期记忆与画像。
- “心法”“戒律”“人格”和能力指令成为有版本、可评测、可回滚的 Prompt 资产。
- 每次已结束会话自动评审，人工抽查校准。
- 系统只生成 Prompt 改进提案，不允许模型未经审批修改生产行为。

Assistant Platform 采用**同仓独立服务**：与 Cursor Pulse 同仓开发和部署，但拥有独立进程、独立 API、独立数据所有权。Pulse 继续负责账号、额度、Cursor Key、借还、引导图、用量与报告；Assistant Platform 负责渠道接入、会话、能力编排、记忆、Prompt、评审和发布。

## 2. 已验证的问题

当前仓库已经具备可复用的记忆、工具和后台基础，但尚未形成完整的助手平台。

### 2.1 会话不可运营

- `pm_conversation_turns` 仅按用户滚动保留约 20 轮。
- 没有独立会话 ID、结束状态和完整时间线。
- 命令、附件处理、普通对话走不同分支，并非所有交互都会进入同一记录链路。
- 后台没有按用户查看完整聊天、工具调用和实际 Prompt 的入口。

### 2.2 能力不可装配

- `pulse/channels/commands.py`、`pulse/channels/dingtalk/handler.py` 和 `pulse/chat/admin_tools.py` 分别硬编码能力。
- 传统命令与自然语言工具调用是两套路由。
- 管理员无法在后台按团队、角色、用户启停能力。
- `admin.dingtalk_user_ids` 与 `portal_role/portal_permissions` 构成权限双轨。

### 2.3 记忆已有地基，但缺少证据化画像

- `personamem` 已有记忆原子、承诺、原则、披露守门和短期对话。
- 当前实现每轮立即提炼，容易产生噪声和额外成本。
- 用户画像没有把事实、偏好、行为模式和预测分层展示。
- 记忆缺少统一的来源消息引用、有效期和用户纠错流程。

### 2.4 自进化尚未与质量闭环连接

- 现有 evolution 从近期记忆与披露日志学习原则和动作。
- 没有会话质量评分、失败分类、人工标注和金标评测集。
- 没有 Prompt 分段、版本、差异、回放、灰度和回滚。
- 当前“自进化”不是基于会话质量的 Prompt 发布系统。

## 3. 已确认的产品决策

1. 原始聊天采用受控完整留存：默认 180 天，可配置。
2. 敏感字段先脱敏；密钥原文不得进入普通聊天存储、日志或模型上下文。
3. 普通用户可查看、导出自己的聊天和画像，并可申请删除或纠正。
4. 第一阶段优先建设能力平台，而不是优先做多助手或全自动进化。
5. 第一版能力由开发者实现，管理员只做目录配置、分配和参数管理。
6. 能力采用团队默认、角色能力包、用户例外三层分配。
7. 工具调用按风险分级；只读可直接执行，写入、敏感和全局操作需要不同级别的确认或审批。
8. 已结束会话全量自动评审，并由人工抽查校准。
9. 会话采用混合结束判定：明确完成/失败立即结束，否则私聊静默 30 分钟、群聊静默 10 分钟后结束。
10. 用户画像必须证据化：带来源、置信度、有效期和纠错能力。
11. Prompt 改进采用提案制：回放评测、人工批准、灰度发布、可回滚。
12. 第一阶段只管理“小脉”，所有核心实体预留 `assistant_id`。
13. 近期规模是单团队 10–50 人。
14. Assistant Platform 采用同仓独立进程，不与 Pulse 共享 ORM 或内部 Python 函数。

## 4. 目标与非目标

### 4.1 目标

- 能力可发现、可配置、可分配、可撤销、可审计。
- 所有渠道进入统一对话运行时。
- 所有交互形成完整、可重放的会话账本。
- 任何真实副作用都可确认、可幂等、可追溯。
- 用户记忆与画像隔离、可解释、可纠正。
- Prompt 行为可版本化、可回放、可灰度、可回滚。
- 自动评审与人工校准形成持续改进证据。
- 在不重写 Pulse 业务能力的前提下渐进迁移。

### 4.2 第一版不做

- 不允许管理员在后台编写或上传任意后端代码。
- 不做无代码工作流编排。
- 不做插件市场。
- 不做多助手管理 UI。
- 不做面向外部企业的 SaaS 多租户平台。
- 不允许模型直接修改生产 Prompt。
- 不允许模型自动生成并安装能力代码。
- 不把模型隐藏思维过程作为审计数据；只记录可验证的简短理由和证据。
- 不迁移或重写 Cursor Pulse 的账号、额度、借 Key、用量与报告领域。

### 4.3 交付拆分

本文是总体架构规格，不应由一个实施计划一次完成。实施必须拆成可独立验收、可回退的子项目：

1. Assistant 服务地基与消息旁路。
2. Capability Registry、策略引擎和三个首批能力。
3. 完整会话账本与后台浏览。
4. personamem 迁移、证据化记忆与画像。
5. 自动评审、人工校准与 Prompt Runtime。
6. Prompt 提案、回放、灰度和回滚。

每个子项目分别编写实施规格或实施计划。第一个实施计划只覆盖“服务地基与消息旁路”，不得把后续五个子项目混入同一次开发。

## 5. 总体架构

```mermaid
flowchart TB
    DT[钉钉 Stream Adapter] --> AP
    WEB[Web Chat Adapter] --> AP

    subgraph AP[assistant-platform 独立服务]
        ORCH[会话编排器]
        REG[Capability Registry]
        POLICY[策略与风险引擎]
        PROMPT[Prompt Runtime]
        MEM[记忆与画像]
        REVIEW[质量评审]
        RELEASE[评测与发布]
        AUDIT[审计与可观测性]
    end

    AP --> ASTORE[(Assistant Store)]
    AP -->|版本化 API + 服务认证 + 幂等键| PULSE[Cursor Pulse Core]
    PULSE --> PDB[(Pulse Store)]
    ADMIN[web-admin 助手中心] --> AP
    ADMIN --> PULSE
```

### 5.1 部署边界

- `assistant-platform` 是独立进程。
- V1 使用独立 SQLite 文件 `data/assistant.db`，启用 WAL。
- V1 的异步收尾任务由同一服务内的持久化任务循环执行，避免多个进程共享 SQLite 文件。
- 数据访问层保持 SQLAlchemy 2 兼容；达到以下任一条件时迁移 PostgreSQL：
  - 活跃用户超过 50；
  - 需要多个 Assistant worker；
  - 需要高可用或跨机器部署；
  - SQLite 写锁成为可观测瓶颈。
- Assistant Platform 与 Pulse 不共享数据库表、ORM Session 或领域对象。
- 两个服务可由现有开发管理器或 Docker Compose 一起启动。

### 5.2 组件职责

#### Channel Adapters

- 解析钉钉和 Web 的原始事件。
- 统一身份、渠道、群/私聊、附件和消息 ID。
- 执行凭证模式识别和预脱敏。
- 转换为标准 `IncomingMessageEvent`。
- 不包含绑定 Key、借 Key、设置引导图等业务分支。

#### Conversation Orchestrator

- 幂等接收消息。
- 开启、续接和结束会话。
- 固定会话 Prompt Release。
- 调用能力解析、模型、工具和回复发送。
- 把每一步写入会话账本。

#### Capability Registry

- 保存能力定义、版本、状态、输入输出契约和 Provider。
- 管理能力包与分配关系。
- 为当前用户和场景计算最终可见能力集合。

#### Policy and Risk Engine

- 在模型调用前过滤能力。
- 在工具执行前再次校验身份、权限、参数、状态和上下文。
- 根据风险级别触发确认或审批。
- 代码层拥有最终决定权，模型无权绕过。

#### Prompt Runtime

- 按固定优先级组装 Prompt。
- 控制各片段版本、Token 预算和内容哈希。
- 生成可复现的 `PromptSnapshot`。

#### Memory and Profile

- 复用并迁移 `personamem`。
- 在会话结束后提炼记忆。
- 维护事实、偏好、事件、承诺、模式和预测。
- 在回答前执行披露守门。

#### Review and Release

- 对已结束会话自动评分。
- 管理人工复核、失败分类和评测数据集。
- 生成 Prompt Diff 提案。
- 运行离线回放、灰度和回滚。

## 6. 身份与隔离

### 6.1 稳定身份

Assistant Platform 内部使用：

- `assistant_id`：V1 固定为 `xiaomai`。
- `team_id`：来自 Pulse 的稳定团队 ID。
- `user_id`：Assistant 内部用户 ID。
- `external_member_id`：Pulse `members.id`。
- `channel_identity`：钉钉 user ID、Web 登录身份等到 `user_id` 的映射。

不得把显示名、邮箱或临时钉钉 sender ID 直接作为长期记忆主键。

### 6.2 场景隔离

- 私聊会话归属单个 `user_id`。
- 群聊会话归属 `channel + conversation_id`，每条消息单独记录发送者。
- 群聊回复只可使用公开记忆和当前群上下文。
- 私聊记忆不得因同一用户出现在群里而进入群 Prompt。
- 对他人的私密画像必须由披露守门物理删除后才能进入主模型上下文。

## 7. 能力模型

### 7.1 三个核心概念

#### Capability

真实、代码支持的原子动作，例如：

- `quota.self.read`
- `cursor.key.bind`
- `cursor.key.unbind`
- `key_loan.request`
- `key_loan.return`
- `guide_image.update`
- `group_message.send`

#### Skill Pack

管理员分配的能力集合，例如：

- `cursor_self_service`
  - 查看本人额度
  - 绑定/解绑本人 Cursor Key
  - 申请/归还 Key
- `assistant_operator`
  - 查看会话
  - 人工复核
  - 管理低风险配置
- `assistant_owner`
  - 更新全局引导图
  - 发布 Prompt
  - 群发和高风险审批

#### Policy

描述谁在什么场景、什么条件下可以使用能力，以及执行前需要什么确认或审批。

### 7.2 Capability Definition

每条能力定义至少包含：

- `key`：稳定标识。
- `display_name`：后台和审计中的名称。
- `version`：契约版本。
- `description`：模型可见的调用说明。
- `input_schema`：JSON Schema。
- `output_schema`：JSON Schema。
- `provider_type`：V1 为 `pulse_http` 或 `assistant_internal`。
- `provider_operation`：来自可信 Provider manifest 的版本化操作标识，不允许管理员填写任意 URL。
- `risk_level`：`read`、`write`、`sensitive`、`destructive`。
- `supported_channels`：允许的渠道。
- `confirmation_policy`：确认要求。
- `approval_policy`：审批要求。
- `timeout_seconds`。
- `retry_policy`：只允许声明安全重试。
- `idempotency_required`。
- `status`：`draft`、`active`、`disabled`、`deprecated`。
- `prompt_instruction`：能力专属说明。
- `config_schema`：团队级可配置参数。

能力定义由受信任 Provider 的签名 manifest 同步，或随 Assistant 代码发布。后台只能启停、配置和分配已注册能力，不能创建指向任意网络地址的能力。

### 7.3 分配解析

最终能力集合由代码按以下顺序计算：

1. 能力及版本必须处于 `active`。
2. 当前渠道、会话类型和上下文必须符合能力限制。
3. 应用团队默认能力。
4. 合并用户所有角色能力包。
5. 应用用户级显式例外：
   - 用户显式 `deny` 删除能力；
   - 用户显式 `allow` 可补充角色或团队未包含的能力；
   - 用户 `allow` 不能覆盖系统禁用、渠道限制或安全硬拒绝。
6. 应用动态策略，如“只能操作本人账号”。
7. 只把最终允许集合的 Schema 发送给 LLM。

未授权能力不只是“调用时拒绝”，而是不会进入模型上下文。

### 7.4 风险分级

#### Read

- 示例：查看本人额度。
- 权限和数据范围校验通过后直接执行。

#### Write

- 示例：申请借 Key。
- 执行前展示动作摘要，由发起人确认。

#### Sensitive

- 示例：绑定 Cursor Key。
- 密钥先进入专用加密存储，模型只拿到 `secret_ref` 和掩码。
- 展示账号、Key hint 和影响范围后确认。
- 任何日志、评审和会话原文都不得包含完整 Key。

#### Destructive or Global

- 示例：更新全局引导图、群发、删除、撤销他人借用。
- 必须具备高风险能力权限。
- 必须显式确认。
- 能力策略可要求另一个审批角色批准。
- 更新类资产保留上一版本，支持回滚。

### 7.5 工具调用状态机

```text
planned
  -> awaiting_confirmation
  -> awaiting_approval
  -> executing
  -> succeeded | failed | unknown | cancelled
```

- 每个调用拥有稳定 `invocation_id` 和 Provider 幂等键。
- `unknown` 表示请求可能已到达 Provider，但响应状态不明。
- `unknown` 不得盲目重试写操作，必须先查询 Provider 状态。
- 同一渠道消息重复投递不得产生第二次副作用。

## 8. 标准消息与会话模型

### 8.1 IncomingMessageEvent

标准事件包含：

- `event_id`
- `channel`
- `channel_message_id`
- `assistant_id`
- `team_id`
- `sender_identity`
- `conversation_type`
- `conversation_id`
- `reply_endpoint`
- `text_redacted`
- `secret_refs`
- `attachments`
- `occurred_at`
- `raw_metadata_redacted`

`channel + channel_message_id` 必须唯一。

### 8.2 会话归属

私聊会话键：

```text
assistant_id + team_id + channel + user_id
```

群聊会话键：

```text
assistant_id + team_id + channel + conversation_id
```

### 8.3 会话开始与结束

- 没有开放会话时，新消息创建会话。
- 有开放会话且未超过超时时间时续接。
- 工具执行明确完成、用户问题明确解决或流程明确失败时，可立即结束。
- 等待确认、审批或异步 Provider 结果时，会话保持开放。
- 私聊静默 30 分钟后结束。
- 群聊静默 10 分钟后结束。
- 新消息到达已结束会话后创建新会话，不重开旧会话。
- 管理员可手动合并或拆分错误切分的会话，变更需审计。

### 8.4 Prompt Release 固定

- 创建会话时固定一个生产 Prompt Release。
- 同一会话默认不切换版本。
- 能力授权每条消息实时重算，撤权立即生效。
- 安全内核紧急修复可强制覆盖所有开放会话，并记录 override。

### 8.5 完整账本

会话账本记录：

- 用户消息和助手回复。
- 附件元数据与安全扫描结果。
- 模型调用、模型版本、请求耗时和失败信息。
- 最终 Prompt Snapshot 的版本清单和哈希。
- 记忆检索到的候选、放行和拦截 ID。
- 能力计划、参数、确认、审批、执行和 Provider 结果。
- 发送状态和渠道回执。
- 会话结束原因。

## 9. 留存、脱敏与访问

### 9.1 默认保留策略

- 原始会话消息：180 天。
- 附件：与原始会话一致，敏感附件可配置更短期限。
- 工具调用与审批审计：至少 365 天。
- 自动评审与人工标签：至少 365 天。
- Prompt Release、发布记录和安全审计：长期保留。
- 记忆与画像：按有效期和用户删除/纠正规则管理。

团队 Owner 可调整期限，但不得超过组织合规上限。

### 9.2 凭证处理

1. Channel Adapter 在持久化和调用 LLM 前识别凭证模式。
2. 原始凭证直接写入专用 Secret Store。
3. 事件正文替换为 `secret_ref` 与掩码。
4. Provider 仅通过受控 Secret Resolver 读取一次。
5. 评审、导出、日志和 Prompt 均只能看到掩码。
6. 无法可靠分类的疑似密钥默认按敏感内容处理。

### 9.3 访问权限

新增能力码：

- `assistant:overview:read`
- `assistant:capabilities:read`
- `assistant:capabilities:write`
- `assistant:sessions:read:self`
- `assistant:sessions:read:all`
- `assistant:sessions:export:self`
- `assistant:sessions:export:all`
- `assistant:reviews:read`
- `assistant:reviews:write`
- `assistant:profiles:read:self`
- `assistant:profiles:read:all`
- `assistant:profiles:correct:self`
- `assistant:profiles:correct:all`
- `assistant:prompts:read`
- `assistant:prompts:write`
- `assistant:prompts:approve`
- `assistant:prompts:publish`
- `assistant:audit:read`

用户访问自己的会话和画像不依赖管理员权限。管理员查看、导出或更正他人数据必须写不可变审计事件。

## 10. 记忆与用户画像

### 10.1 personamem 的新边界

- `personamem` 继续作为独立领域包。
- Pulse 不再直接装配 `MemoryEngine`。
- Assistant Platform 通过适配层装配存储、LLM 和身份。
- 现有记忆 ID、承诺和披露日志可迁移后保留。

### 10.2 会话结束后提炼

当前每轮提炼改为：

1. 会话结束。
2. 会话摘要器识别任务、结果和未完成事项。
3. 记忆提炼器生成候选事实、偏好、事件和承诺。
4. 确定性后处理添加来源、可见性、置信度和有效期。
5. 与已有记忆去重、合并或 supersede。
6. 写入记忆并保留证据指针。

提炼失败不影响已完成聊天，可重试。

### 10.3 记忆类型

- `fact`：相对稳定的事实。
- `preference`：表达方式、工具或协作偏好。
- `event`：发生过的事件。
- `commitment`：助手对用户的应允或拒绝。
- `behavior_pattern`：多个事件形成的重复模式。
- `prediction`：基于模式产生的可失效推断。

### 10.4 证据字段

每条记忆或画像信号至少包含：

- `subject_id`
- `kind`
- `content`
- `evidence_message_ids`
- `source_session_ids`
- `confidence`
- `valid_from`
- `expires_at`
- `visibility`
- `sensitivity`
- `status`
- `supersedes_id`
- `created_by`

### 10.5 行为模式示例

“该用户常在每月中旬额度不足”应存为：

- 类型：`behavior_pattern`。
- 证据：近 4 个账期中 3 次借 Key。
- 置信度：例如 0.86。
- 有效期：90 天。
- 可选动作：中旬前提醒额度。

它不是一段无法解释的模型总结。

### 10.6 用户纠错

用户可对自己的记忆：

- 确认正确。
- 修改表述。
- 标记错误。
- 隐藏，不再进入回答上下文。
- 申请删除。

纠错后旧记忆退出检索，但保留最小审计信息；审计信息不得继续包含已要求删除的正文。

## 11. Prompt 架构

### 11.1 分层与优先级

Prompt 从高到低分为：

1. **P0 安全内核**
   - 隐私、权限、数据边界、不得伪造工具结果。
   - 只能由代码评审和发布。
   - AI 不得提出自动修改。
2. **P1 人格**
   - 名称、角色、语气、作息、关系定位。
3. **P2 心法**
   - 做事原则，如实事求是、先澄清边界、克制表达、保持一致。
4. **P3 戒律**
   - 可观察的必须项、禁止项和冲突处理规则。
5. **P4 场景策略**
   - 群聊、私聊、Web、确认、审批等场景规则。
6. **P5 能力说明**
   - 仅注入当前用户被授权能力的说明和 Schema。
7. **P6 记忆与画像**
   - 仅注入披露守门放行的证据化内容。
8. **P7 当前会话**
   - 当前会话摘要、近期消息和用户最新输入。

冲突时高优先级覆盖低优先级。

### 11.2 Prompt 资产

核心实体：

- `prompt_fragments`
  - 分段内容、类型、版本、状态、Token 预算。
- `prompt_releases`
  - 一组已锁定 fragment 版本。
- `prompt_snapshots`
  - 某次模型调用最终使用的版本清单、哈希和动态上下文引用。
- `prompt_change_proposals`
  - 候选 Diff、来源失败簇、预期改善、风险说明。
- `prompt_deployments`
  - 灰度比例、目标用户、起止时间、状态和回滚目标。

### 11.3 心法与戒律的约束

- 心法描述稳定的做事方式，不要求模型展示隐藏思维过程。
- 戒律必须可观察、可测试，避免“永远聪明”“保持完美”等空泛表述。
- 单条戒律应只表达一个行为约束。
- 能力专属规则放在能力说明，不堆入全局戒律。
- 每个片段有独立 Token 预算，发布时执行长度门禁。

## 12. 会话质量评审

### 12.1 触发

- 会话结束后创建持久化评审任务。
- 每个已结束会话必须有自动评审状态。
- 评审失败可重试，不影响聊天回复。

### 12.2 评分维度

默认 rubric：

- 任务完成度：30%。
- 能力选择与工具执行正确性：20%。
- 事实依据与记忆使用：15%。
- 隐私、安全和权限：20%。
- 人格、温度和沟通质量：10%。
- 简洁与效率：5%。

隐私泄漏、越权执行、伪造工具成功属于硬失败，不得被其他高分抵消。

Rubric 本身版本化，每条评审记录保存 evaluator 版本和 rubric 版本。

### 12.3 评审输出

自动评审至少包含：

- 总分和各维度分。
- `task_status`：`succeeded`、`partial`、`failed`、`unknown`。
- 失败分类。
- 证据消息和工具调用 ID。
- 做得好的地方。
- 可操作的改进建议。
- 是否需要人工复核。
- 是否适合加入评测集。

只给总分而没有证据的评审无效。

### 12.4 防止评审偏差

- 生成模型与 evaluator 使用独立 Prompt。
- 条件允许时使用不同模型或至少不同推理配置。
- 权限、工具状态、凭证泄漏、消息重复等由确定性代码检查。
- 人工复核持续校准 evaluator。
- 人工标签优先于自动标签。

### 12.5 人工抽查队列

以下会话强制进入队列：

- 任一安全硬失败。
- 总分低于 70。
- 工具调用状态为 `unknown`。
- 新能力上线后的前 20 次调用。
- 新 Prompt 灰度样本。
- 用户显式差评。

其他会话默认随机抽样 10%，比例可配置。

## 13. 安全的 Prompt 进化

### 13.1 三种进化必须分离

- 用户记忆：可自动提炼，但带证据、置信度和纠错。
- 全局 Prompt：只自动生成候选，必须评测和批准。
- 后端能力：只能由开发者发布代码版本。

### 13.2 提案触发

满足任一条件可创建提案：

- 管理员从会话或失败簇手动发起。
- 同一失败分类在 14 天内至少出现 3 次。
- 新能力的评审结果持续低于基线。
- 人工复核发现系统性问题。

单次低质量会话不得自动生成全局生产规则。

### 13.3 提案内容

- 目标 Prompt fragment。
- 最小文本 Diff。
- 来源会话和失败簇。
- 问题假设。
- 预期改善指标。
- 可能退化的行为。
- 新增或修改的回放用例。
- 风险级别。

### 13.4 离线回放

候选版本与当前生产版本同时运行：

- 目标失败簇样本。
- 通用金标会话。
- 权限和隐私红队集。
- 代表性成功会话，防止已有能力退化。

晋级门禁：

- 安全硬失败必须为 0。
- 总体任务成功率不得下降。
- 目标失败簇成功率至少相对提升 10%。
- 关键能力的契约检查全部通过。
- 人格一致性不得低于当前版本。

### 13.5 审批、灰度与回滚

1. 具备 `assistant:prompts:approve` 的人员审阅 Diff 和回放报告。
2. 批准后默认对 10% 新会话灰度。
3. 灰度至少运行 24 小时且覆盖 20 个已结束会话；样本不足时继续运行。
4. 任何安全硬失败立即自动停止灰度并回滚。
5. 其他核心指标低于门禁时停止灰度。
6. 达标后具备 `assistant:prompts:publish` 的人员晋级生产。
7. 回滚必须在一分钟内完成，并只影响新会话；开放会话继续使用已固定版本，安全紧急回滚除外。

## 14. Assistant Console

现有 `web-admin` 保留统一登录和外壳，新增“助手中心”导航分组。前端通过 Assistant API 访问助手域，通过 Pulse API 访问业务域。

### 14.1 助手总览

- 当前 Prompt Release 和健康状态。
- 24 小时会话数、任务成功率、低质量会话和策略拦截。
- 待人工复核会话。
- 重复失败簇。
- 待审批 Prompt 提案。
- 能力调用失败和 Provider 健康。

### 14.2 能力中心

- 能力目录。
- 能力版本和 Provider 状态。
- 能力包。
- 团队、角色、用户分配。
- 风险与确认策略。
- 配置参数。
- 调用日志和成功率。

### 14.3 会话与评审

- 按用户、时间、渠道、能力、评分、失败分类和状态筛选。
- 完整消息时间线。
- 工具调用、确认、审批和 Provider 结果。
- Prompt Release、模型和记忆披露。
- 自动评分证据。
- 人工标注和改进提案入口。

### 14.4 用户与画像

- 用户基本信息和能力包。
- 会话历史。
- 事实、偏好、事件、承诺、行为模式和预测。
- 证据、置信度和有效期。
- 纠错、隐藏和删除。

### 14.5 Prompt Studio

- P1–P5 分段编辑。
- 版本历史和 Diff。
- Token 预算。
- 回放报告。
- 审批、灰度、晋级和回滚。
- P0 安全内核只读展示，修改走代码发布流程。

### 14.6 评测与发布

- 金标数据集。
- 红队数据集。
- 评测运行和版本对比。
- 失败聚类。
- Prompt 提案。
- 灰度状态与指标。

### 14.7 渠道、模型、审计与保留

- 钉钉/Web 渠道状态。
- 生成模型和 evaluator 配置。
- 数据保留期限。
- 查看、导出、删除和发布审计。
- Provider 服务认证和健康状态。

## 15. API 与集成契约

### 15.1 Assistant API

API 统一使用 `/api/assistant/v1`：

- `POST /events/messages`
- `GET /sessions`
- `GET /sessions/{id}`
- `POST /sessions/{id}/close`
- `GET /sessions/{id}/reviews`
- `POST /sessions/{id}/human-review`
- `GET /users/{id}/profile`
- `POST /users/{id}/profile-corrections`
- `GET /capabilities`
- `POST /capabilities/{key}/versions`
- `POST /capability-packs`
- `POST /capability-assignments`
- `GET /tool-invocations`
- `POST /tool-invocations/{id}/confirm`
- `POST /tool-invocations/{id}/approve`
- `GET /prompt-fragments`
- `POST /prompt-change-proposals`
- `POST /evaluation-runs`
- `POST /prompt-deployments`
- `POST /prompt-deployments/{id}/promote`
- `POST /prompt-deployments/{id}/rollback`

外部 API 使用 JWT 用户身份；服务内部 API 使用独立服务凭证。

### 15.2 Pulse Capability Provider API

Pulse 新增 `/api/internal/v1/capabilities`：

- 查询 Provider manifest。
- 调用指定能力版本。
- 查询异步或未知调用状态。

每次调用必须包含：

- `invocation_id`
- `idempotency_key`
- `team_id`
- `actor_member_id`
- `capability_key`
- `capability_version`
- `arguments`
- `confirmed_by`
- `approved_by`
- `requested_at`

Pulse 必须自行再次校验 actor、数据范围和业务规则，不能只相信 Assistant Platform。

### 15.3 Provider Result

统一返回：

- `status`：`succeeded`、`failed`、`pending`、`unknown`。
- `user_message`：可展示给用户的结构化信息。
- `result`：符合 output schema 的数据。
- `error_code`。
- `retryable`。
- `provider_reference`。
- `completed_at`。

模型不得根据异常文本推测成功。

## 16. Assistant Store 数据模型

### 16.1 身份与助手

- `assistants`
- `assistant_teams`
- `assistant_users`
- `channel_identities`

### 16.2 会话与消息

- `chat_sessions`
- `chat_messages`
- `chat_attachments`
- `model_invocations`
- `prompt_snapshots`
- `outbox_events`
- `background_jobs`

### 16.3 能力

- `capability_definitions`
- `capability_versions`
- `capability_configs`
- `capability_packs`
- `capability_pack_items`
- `capability_assignments`
- `tool_invocations`
- `tool_confirmations`
- `tool_approvals`

### 16.4 Prompt 与评测

- `prompt_fragments`
- `prompt_releases`
- `prompt_change_proposals`
- `prompt_deployments`
- `review_rubrics`
- `session_reviews`
- `human_reviews`
- `failure_clusters`
- `evaluation_datasets`
- `evaluation_cases`
- `evaluation_runs`
- `evaluation_results`

### 16.5 记忆与审计

- 迁移并扩展现有 `pm_*` 表。
- `profile_signals`
- `profile_corrections`
- `assistant_audit_events`
- `data_deletion_requests`

所有核心实体包含 `assistant_id` 和 `team_id`，即使 V1 只运行一个助手和一个团队。

## 17. 可靠性与错误处理

### 17.1 消息先入账后处理

- 标准事件先以唯一键持久化。
- 会话、消息和处理任务在同一事务创建。
- 回复通过 outbox 发送。
- 服务崩溃后可从未完成任务继续。

### 17.2 模型失败

- 超时或暂时错误可按配置重试。
- 工具已执行后，不得因回复模型失败重新执行工具。
- 降级回复必须明确当前真实状态。
- 无法确定时说“状态待确认”，不得编造成功。

### 17.3 Provider 失败

- `retryable=false` 直接失败并给出可操作提示。
- `retryable=true` 的只读操作可安全重试。
- 写操作只有 Provider 声明幂等且携带相同幂等键时才能重试。
- 响应丢失进入 `unknown`，先查状态。

### 17.4 记忆与评审失败

- 均为异步任务。
- 采用持久化重试次数和退避。
- 超过阈值进入后台待处理，不影响会话账本。
- 重试操作必须幂等。

### 17.5 配置与发布失败

- Prompt Release 和能力配置发布使用事务。
- 发布未完成时新会话继续使用上一稳定版本。
- 灰度和生产均保留明确 rollback target。

## 18. 安全设计

- Assistant 与 Pulse 使用独立服务认证，凭证定期轮换。
- 用户 JWT 不能直接调用 Pulse internal API。
- Pulse 对所有副作用重新做授权和业务校验。
- 能力参数按 JSON Schema 严格校验，拒绝额外字段。
- 秘密在进入日志、数据库普通字段和 LLM 前脱敏。
- 私聊正文、附件和画像敏感字段使用应用层 AES-GCM 加密后落盘；加密密钥与数据库文件分离保存并支持轮换。
- 列表页只查询最小必要元数据；解密正文必须通过授权接口，并记录访问审计。
- 自动评审只接收已脱敏内容，遵守与生成模型相同的数据边界。
- 群聊上下文不包含私聊记忆。
- 能力 Schema 只按最终授权集合注入。
- 所有查看他人会话、导出、纠错、审批和发布行为审计。
- 删除请求覆盖原始会话、附件和派生内容；法律或安全审计只保留不含正文的最小记录。
- 安全内核的修改必须通过代码评审和安全回归测试。

## 19. 测试策略

### 19.1 确定性单元测试

- 分配解析和显式拒绝。
- 系统禁用不能被用户允许覆盖。
- 渠道和数据范围限制。
- 风险状态机。
- 会话混合结束判定。
- Prompt 分层优先级和版本固定。
- 密钥识别、脱敏和 Secret Ref。
- 消息和工具幂等。
- 记忆证据、有效期和纠错。
- 留存与删除。

### 19.2 契约测试

- Assistant Capability Definition 与 Pulse manifest 一致。
- 输入输出 JSON Schema。
- Provider 状态与错误语义。
- 服务认证和 actor 传递。
- 版本兼容和废弃流程。

### 19.3 集成测试

- 钉钉文本 → 会话 → 能力 → Pulse → 回复。
- Web Chat 使用同一路径。
- 重复消息不重复执行。
- 用户确认和管理员审批。
- 模型超时、Provider 超时和响应丢失。
- 会话结束后记忆提炼和自动评审。

### 19.4 安全与红队

- Prompt 注入要求展示未授权工具。
- 伪造管理员身份。
- 诱导泄漏他人私聊记忆。
- 诱导输出完整 Cursor Key。
- 群聊查询个人敏感画像。
- 利用重复回调触发重复群发或绑定。
- 利用模型文本伪造工具成功。

### 19.5 回归评测

- 每个已修复的真实失败至少转成一个评测用例。
- 金标集包含成功、拒绝、澄清、确认、工具失败和隐私回避。
- Prompt 发布前比较生产基线与候选版本。
- 安全硬失败为零容忍门禁。

## 20. 迁移路线

### 阶段 0：边界与旁路

交付：

- 新建 Assistant Platform 独立服务和 Assistant Store。
- 定义统一身份、消息和 Provider 契约。
- 钉钉事件旁路镜像到 Assistant Platform，但仍由旧 handler 回复。
- 建立消息幂等、outbox 和基础审计。
- 修复 `admin.dingtalk_user_ids` 空列表导致全员管理员的风险。

回退：

- 停止旁路即可，现有 Bot 行为不变。

### 阶段 1：能力中心优先

交付：

- Capability Registry、能力包、分层分配和风险闸门。
- Pulse internal Provider API。
- 首批三个代表能力：
  - `quota.self.read`
  - `cursor.key.bind`
  - `guide_image.update`
- 后台能力目录和分配页面。
- 统一 portal 与钉钉权限来源。

迁移方式：

- 旧命令通过兼容桥转发到 Assistant 能力执行链。
- 每个能力单独 feature flag 切换。

### 阶段 2：完整会话账本

交付：

- Assistant Platform 接管钉钉和 Web Chat 主路由。
- 文本、命令、附件、工具、错误全部进入统一会话。
- 会话后台、筛选、详情和用户自助查看。
- 180 天保留、凭证脱敏、导出和删除。

迁移方式：

- 新旧回复在影子模式比较。
- 切换后保留旧 handler 快速回退开关。

### 阶段 3：记忆与画像

交付：

- `personamem` 迁入 Assistant 装配边界。
- 将现有 `pm_*` 数据迁移到 Assistant Store。
- 会话结束后提炼。
- 证据、有效期、模式、预测和用户纠错。
- 群/私聊披露回归测试。

### 阶段 4：评审与 Prompt Studio

交付：

- 自动评审、人工抽查和失败分类。
- Prompt 分层、版本、Release 和 Snapshot。
- 金标/红队数据集与离线回放。
- Prompt Studio 和发布审批。

### 阶段 5：受控进化

交付：

- 失败聚类。
- Prompt Diff 提案。
- 灰度、门禁、晋级和回滚。
- 删除旧命令分流、旧 ChatService 工具路由和权限双轨。

每个阶段必须能独立运行和回退，不允许大爆炸式切换。

## 21. 验收标准

### 能力平台

- 管理员可在 5 分钟内把已注册能力加入角色或指定用户，无需部署。
- 能力撤销后下一条消息立即失效。
- 未授权能力不进入 LLM 工具列表。
- 三个首批能力使用同一注册、策略、确认和审计链路。

### 会话与审计

- 后台可查看每个用户完整会话。
- 命令、普通聊天、附件、错误和工具结果全部归入时间线。
- 任一助手回复可追溯到 Prompt Release、模型、记忆证据和工具结果。
- 重复消息不会产生重复副作用。

### 记忆与画像

- 用户之间的私聊记忆严格隔离。
- 群聊不能披露私聊机密。
- 画像模式带证据、置信度和有效期。
- 用户可查看和纠正自己的记忆。

### 质量与进化

- 所有已结束会话产生自动评审结果或明确失败状态。
- 自动评分包含证据和失败分类。
- 低分、高风险、新能力和灰度会话进入人工队列。
- Prompt 候选必须通过离线回放和人工批准。
- Prompt 版本可灰度并在一分钟内回滚。
- AI 无法直接修改安全内核或生产 Prompt。

### 安全

- 完整 Cursor Key 不进入普通消息表、日志、评审或模型上下文。
- 越权工具执行测试为零容忍。
- Pulse 对副作用执行二次授权。
- 查看、导出、删除、审批和发布均可审计。

## 22. 关键代码迁移映射

- `pulse/channels/dingtalk/handler.py`
  - 收缩为 Channel Adapter；删除业务关键词分流。
- `pulse/channels/commands.py`
  - 业务操作迁入 Pulse Provider API；命令语法变为兼容入口。
- `pulse/chat/service.py`
  - 由 Assistant Conversation Orchestrator 替代。
- `pulse/chat/admin_tools.py`
  - 功能迁入 Capability Registry 与 Provider。
- `pulse/chat/planner.py`
  - 由 Prompt Runtime 的授权工具规划替代。
- `pulse/memory_adapter/*`
  - 移入 Assistant Platform 适配层。
- `personamem/*`
  - 保持独立包，扩展证据、有效期和会话结束提炼。
- `pulse/web/permissions.py`
  - 权限来源统一；Assistant 权限由 Assistant API 管理或同步。
- `pulse/web/app.py`
  - 不再直接装配 ChatService；Web Chat 代理到 Assistant API。
- `web-admin/src/*`
  - 增加助手中心路由、API client 和权限导航。

## 23. 决策结论

“最完美”的系统不是功能最多或服务拆得最细，而是让以下闭环成立：

```text
开发者注册可靠能力
  -> 管理员安全装配
  -> 助手按授权服务用户
  -> 所有过程可观察
  -> 会话结束后形成记忆和质量证据
  -> AI 提出最小改进
  -> 人工与评测把关
  -> 灰度发布并可回滚
```

该设计优先解决用户选择的“能力平台化”，同时为完整会话、证据化记忆和安全进化提供同一地基。
