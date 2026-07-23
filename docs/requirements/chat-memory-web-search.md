# 聊天记忆与联网搜索需求

> **版本**：v1（需求基线）  
> **日期**：2026-07-17  
> **状态**：阶段 1 已固化；实现分阶段推进  
> **关联计划**：`.cursor/plans/chat-memory-search_4303f1a7.plan.md`  
> **稳定契约**：`assistant_platform/memory/contracts.py`  
> **Assistant 配置**：`assistant_platform/config.py` → `AssistantChatMemoryConfig`  
> **联网密钥**：Tavily API Key 仅存在于 Pulse 配置层（`pulse/config.py` / Secret Store），不得写入 Assistant 日志、会话状态或审计正文

---

## 1. 目标与非目标

### 1.1 目标

- 关闭会话的脱敏原文**永久归档**，支持导出、纠正与级联删除。
- 私聊形成**个人记忆**；群聊形成**群/团队共享记忆**；禁止用群聊推断个人画像。
- 关闭后生成带证据的结构化摘要、事实/承诺与交互画像候选。
- **每个用户回合**自动混合召回少量高价值历史，模型可通过工具渐进展开。
- 联网搜索采用**混合触发**，经 capability 链路首接 Tavily；网页内容不自动写入长期记忆。
- 首版面向本地中小规模（单团队 10–50 人），存储与检索接口保留迁移到 pgvector / OpenSearch 的边界。

### 1.2 非目标（首版明确不做）

- 跨团队共享记忆、全网持续爬取、多搜索提供商融合。
- 心理学人格诊断、敏感人格标签推断。
- 基于画像自动执行外部动作；使用私人会话训练模型。
- 搜索结果或网页正文自动落库为长期记忆。

---

## 2. 数据分类

| 层级 | 内容 | 保留策略 | 可检索 | 备注 |
|------|------|----------|--------|------|
| **运行账本** | `ap_chat_sessions` / `ap_chat_messages` 中未关闭或近期消息 | 默认 180 天；**仅删除已成功归档的消息** | 否（活跃会话内上下文除外） | 由 `retention.py` 清理；永久档案不参与此清理 |
| **永久档案（原文）** | 关闭后会话全部脱敏消息快照，带稳定 `seq` | **永久**，直至用户/管理员删除 | 间接（经片段索引） | 含 tool/interim 等全角色；审计与导出用 |
| **检索片段** | 由档案派生的 FTS/向量索引单元 | 随档案；删除级联清除 | **是** | 默认仅 **用户消息 + final 助手回复** 进入检索；按对话回合分块，超长按 token 切分 |
| **结构化摘要** | 主题、目标、结果、未完成项等 | 随档案 | 经工具/API | 每项带证据引用与置信度 |
| **语义记忆** | `ap_semantic_atoms` / `ap_commitments` 事实、承诺、偏好 | 长期；删除级联 | 是（经披露守门） | `assistant_platform/memory/semantic/`；证据字段与档案流水线对齐 |
| **交互画像** | 可观察交互偏好编译结果 | 长期；证据失效则降级/撤销 | 注入上下文（压缩） | 与候选信号分离；见 §6 |
| **联网结果** | Tavily 搜索/抓取返回 | **不持久化**（短时缓存可选） | 否 | 仅当次回合上下文；不可信数据 |
| **审计** | 删除、搜索、召回、披露记录 | 长期 | 否 | **不得含正文、密钥或完整搜索词** |

### 2.1 归档流水线阶段

关闭会话后异步执行，阶段独立重试、幂等写入：

| 阶段 | 产出 | 状态字段 |
|------|------|----------|
| `archive` | 脱敏原文快照、`seq`、内容哈希 | `pending → partial → ready \| failed` |
| `index` | FTS5 + 向量索引 | 同上 |
| `summary` | `SessionSummary` 结构化结果 | 同上 |
| `facts` | 语义记忆事实/承诺候选（`ap_semantic_*`） | 同上 |
| `profile` | 交互画像信号（私聊 only） | 同上 |

终态：`ready`（可召回）、`failed`（可重试）、`deleted`（用户删除后）。

聊天关闭**不等待** embedding 或总结完成。

### 2.2 分块规则

1. 基本单位：**一个用户消息 + 对应 final 助手回复**（一个对话回合）。
2. 超长回合按 `chunking.max_tokens_per_chunk` 切分，保留 `start_seq` / `end_seq` 与源消息 ID。
3. tool 调用、interim 回复、系统消息：**写入永久档案**，默认**不进入**检索片段（除非未来显式开关）。
4. 索引版本变更时按 `archive.index_version` 后台重建；新索引就绪后切换，旧索引再淘汰。

---

## 3. 作用域（Scope）

### 3.1 分域模型

| 场景 | 记忆归属 | 召回可见性 | 画像 |
|------|----------|------------|------|
| **私聊** | `scope=personal`，`subject_id=user_id` | 仅该用户在同 team 下的私聊档案与 personal 语义记忆 | 允许提炼交互画像 |
| **群聊** | `scope=group`，`subject_id=conversation_id`（或群标识） | 同群/同 team 参与者可见 group 档案与 public 语义记忆 | **禁止**从群聊推断个人画像 |
| **团队** | 所有数据带 `team_id` | 跨 team **严格隔离** | 团队级开关可禁用记忆 |

### 3.2 召回过滤顺序

1. `team_id` 必须匹配当前团队。
2. `scope` 与当前 `conversation_type`（private/group）一致。
3. 私聊仅返回 `subject_id == 当前 user_id` 的 personal 数据。
4. 群聊返回 group scope；不混入其他用户的 personal 数据。
5. 排除 `deleted` 档案与当前**未关闭**会话（避免重复注入）。
6. 结果经 `assistant_platform/memory/semantic/gate.py` **披露守门**（群场景 fail-closed）。

### 3.3 统一语义记忆架构

记忆运行时已全部收敛到 **Assistant Platform**，Pulse 不再依赖独立 `personamem` 包或 `pulse/memory_adapter/`：

| 模块 | 职责 |
|------|------|
| `assistant_platform/memory/semantic/domain.py` | 值对象：`VisibilityContext`、`SemanticAtom`、`Commitment` 等 |
| `assistant_platform/memory/semantic/repository.py` | `ap_semantic_*` 表 CRUD |
| `assistant_platform/memory/semantic/distill.py` | 关闭后提炼写入 |
| `assistant_platform/memory/semantic/gate.py` / `recall.py` | 披露守门与召回 |
| `assistant_platform/memory/identity.py` | `team_id → namespace`、钉钉群/私聊场景映射 |
| `assistant_platform/memory/archive_search.py` | 档案 FTS/向量检索，与语义召回融合为 `RecallBundle` |

- 命名空间继续映射 `team_id` → `namespace`（`team:{team_id}`）。
- 群聊 `SourceVisibility.PUBLIC`；私聊 `SourceVisibility.PRIVATE`。
- 管理端**唯一总开关**在「聊天记忆」模态（`chat_memory.archive.enabled` 与各 `features.*`）；旧 `assistant_llm.memory_enabled` / `ASSISTANT_MEMORY_ENABLED` 仅作兼容：`false` 可强制关闭归档特性；`true` 仅影响 `resolve_effective_memory_enabled()`（仪表盘），**不会**默认启动归档流水线。
- 遗留 `pm_*` 表可通过 `assistant_platform/memory/semantic/migrate.py` 一次性迁入 `ap_*`。

---

## 4. 删除语义

### 4.1 删除粒度

| 操作 | 影响范围 | 审计 |
|------|----------|------|
| 删除单条记忆（atom/fact） | 该 atom 及仅指向它的证据链接 | 记录 ID，无正文 |
| 删除单场会话 | 档案原文、FTS、向量、摘要、派生 facts、画像证据；失去支撑的画像项降级 | 记录 session_id |
| 删除全部个人记忆 | 该用户 personal scope 下全部上述数据 | 记录 user_id |
| 关闭后续记忆 | 停止新归档/提炼；已有数据保留直至显式删除 | 记录 opt-out 时间 |

### 4.2 级联规则

删除会话或用户记忆时必须同步清除：

- 永久档案表与消息快照
- FTS5 虚拟表条目（使用 SQLite `trigram` 分词器，支持中文子串匹配；语义召回仍依赖向量索引；迁移 OpenSearch 时可进一步改进中文分词）
- 向量索引条目
- 结构化摘要与流水线状态
- `ap_semantic_*` 中证据指向该会话的 atoms/commitments（或标记 superseded）
- `ap_profile_signals` 中 `source_session_ids` 含该会话的条目；重新编译生效画像
- 召回/搜索短时缓存

**保留**：不含正文的删除审计、披露日志摘要（ID 列表级）。

### 4.3 运行账本

- 180 天 retention **仅** purge 已成功归档 (`archive status >= ready`) 的旧 `ap_chat_messages`。
- 未归档或归档失败的会话消息**不得**被 retention 删除。

---

## 5. 片段返回结构

所有召回 API、工具与 `RecallBundle` 中的片段使用统一契约（见 `contracts.ArchiveHit`）。

### 5.1 必填字段

| 字段 | 说明 |
|------|------|
| `memory_id` | 片段稳定 ID（chunk ID） |
| `session_id` | 来源会话 |
| `source_type` | `archive_chunk` / `fact` / `commitment` / `preference` / `profile` |
| `scope` | `personal` / `group` |
| `text` | 脱敏片段正文 |
| `source_roles` | 参与角色，如 `("user", "assistant")` |
| `occurred_from` / `occurred_to` | 片段时间范围（UTC） |
| `start_seq` / `end_seq` | 会话内稳定消息序号 |
| `chunk_index` | 会话内片段序号（0-based） |
| `session_message_total` | 该会话归档消息总数 |
| `session_chunk_total` | 该会话检索片段总数 |
| `rank` | 融合排序名次（1-based） |
| `score` | 融合分 |
| `confidence` | 可选；事实/画像来源时使用 |
| `has_prev` / `has_next` | 相邻片段是否存在 |
| `anchor` | `ChunkAnchor`，用于 expand / read_range |

### 5.2 分页与游标

搜索与首轮注入附带 `SearchPageMeta`：

| 字段 | 说明 |
|------|------|
| `total_hits` | 本次查询命中总数（过滤后） |
| `returned_count` | 本页返回数 |
| `has_more` | 是否还有下一页 |
| `cursor` | `RecallCursor`：稳定排序键 + offset；**同一查询条件下分页稳定** |

`RecallCursor` 编码：`query_fingerprint`、`sort_key`、`offset`。禁止依赖数据库 rowid。

### 5.3 渐进展开

| 工具 | 行为 |
|------|------|
| `memory.search` | 关键词/时间过滤/游标续页 |
| `memory.expand` | 以 `ChunkAnchor` 为中心前后 N 个片段 → `NeighborWindow` |
| `memory.get_session_summary` | 返回 `SessionSummary`，不返回全文 |
| `memory.read_range` | 按 `start_seq`–`end_seq` 读取；需二次权限与披露检查 |

建议披露顺序：**少量命中预览 → 相邻上下文 → 会话摘要 → 指定范围原文**。

### 5.4 首轮注入预算

- 默认 ≤ **3** 个历史片段、若干稳定事实、一份压缩画像。
- 总 token ≤ `recall.context_token_budget`（默认 1500）。
- 同一会话默认最多 **2** 个片段命中。
- 重叠片段合并；召回失败时**静默降级**，模型不得暗示“记得”。

---

## 6. 交互画像白名单

### 6.1 允许维度（`ProfileDimension`）

| 维度 | 说明 |
|------|------|
| `addressing` | 称呼偏好 |
| `language` | 语言 |
| `formality` | 正式程度 |
| `verbosity` | 回复详略 |
| `structure` | 结构偏好（列表/段落/步骤） |
| `examples` | 示例偏好 |
| `proactivity` | 主动程度 |
| `confirmation` | 确认习惯 |
| `decision_style` | 决策方式 |
| `domain_familiarity` | 领域/技术熟悉度 |
| `explicit_taboo` | 用户明确禁忌 |

### 6.2 禁止内容

健康、政治、宗教、种族、性取向等**敏感人格标签**；心理学诊断；不可观察的性格特质（如“内向/外向 Big Five”）。

### 6.3 生效规则

优先级：**用户明确纠正 > 最新明确表达 > 多次一致观察 > 单次推断**。

- 用户明确表达的偏好可立即进入生效画像。
- 推断性信号需达到置信度阈值且具可追溯证据。
- 注入模型的是**压缩交互指导**，非心理学评价；优先级低于安全规则与用户当前指令。
- **群聊不参与**个人画像提炼。

---

## 7. 联网搜索（Tavily）

### 7.1 配置边界

- Tavily API Key、endpoint、rate limit：**仅 Pulse 配置层**（环境变量 / Secret Store / `pulse/config.py`）。
- Assistant 侧仅保留 capability 触发策略与功能开关引用，**不存储密钥**。
- 日志与会话：`web.search` / `web.fetch` 审计记录提供商状态、耗时、结果条数；**不记录完整 query、网页正文或 API Key**。

### 7.2 能力

- `web.search`：查询 Tavily，返回标准化标题、URL、域名、摘要、发布时间（若有）、检索时间、排名。
- `web.fetch`：HTTP/HTTPS 安全抓取；拦截本机/内网/云元数据；限制大小、超时、Content-Type。

### 7.3 触发规则

| 条件 | 行为 |
|------|------|
| 用户明确要求搜索/联网/核实 | **必须**调用 |
| 时效性信息、需具体来源、模型明显不确定 | **允许**自动调用 |
| 用户明确禁止联网 | **不得**调用 |
| 搜索失败 | 直说失败，禁止用旧知识伪装搜索结果 |

### 7.4 隐私

- 搜索词默认仅来自当前用户消息；**不得**将私人历史、画像或机密注入外部搜索请求。
- 网页内容按不可信数据处理；不能覆盖系统指令。
- 搜索结果**不自动**写入长期记忆。

---

## 8. 性能与可靠性指标

| 指标 | 目标 |
|------|------|
| 每回合记忆召回延迟 P95 | ≤ **500 ms**（本地中小规模） |
| 关闭后会话归档完成（ready） | **95%** 在 **2 分钟**内 |
| 归档流水线最终成功率 | ≥ **99.5%** |
| 首轮记忆注入 token | ≤ **1500**（可配置） |
| 联网搜索超时 | 默认 **10 s**；超时不阻塞聊天 |
| Embedding | 批量生成；按内容哈希去重；**无 OpenAI Key 时暂用 `HashingEmbedder`（本地/dev 过渡方案），配置 `embedding.model` + LLM Key 后自动切换 OpenAI 兼容嵌入**

### 8.1 可观测性（不含正文）

- 归档各阶段状态与耗时、片段数、索引版本
- 召回来源（fts/vector/fact/profile）与命中数
- 上下文 token 估算、expand 次数
- 搜索提供商状态、失败原因、重试次数

---

## 9. 验收场景

1. **精确 + 语义召回**：关闭会话后，新回合分别通过精确词与语义改写找回相关内容。
2. **片段元数据完整**：命中含时间、序号、总数、前后关系；`expand` 连续且不重复乱序。
3. **私聊/群聊隔离**：私聊记忆不出现在群聊；team A 无法检索 team B。
4. **画像纠正**：用户纠正偏好后旧画像失效，下一回合按新偏好交互。
5. **级联删除**：删除会话后，FTS/向量/摘要/画像均不可再返回其内容。
6. **联网引用**：时效性问题触发 Tavily；回答含来源与检索时间。
7. **SSRF 与注入**：恶意 URL 与网页 prompt  injection 不能改变策略或触发未授权工具。
8. **历史回填**：对已关闭且仍有原文的会话，回填命令可重建索引且幂等。
9. **retention 安全**：已归档消息可被 ledger 清理；未归档消息保留。
10. **召回降级**：索引不可用时不阻塞聊天，且模型不虚假声称记忆。

---

## 10. 配置参考（Assistant）

环境变量前缀 `ASSISTANT_CHAT_MEMORY_*`，详见 `AssistantChatMemoryConfig`：

| 分组 | 关键项 |
|------|--------|
| `archive` | `enabled`, `index_version`, `ledger_retention_days` |
| `chunking` | `max_tokens_per_chunk`, `overlap_tokens` |
| `embedding` | `enabled`, `model`, `batch_size`, `dedupe_by_content_hash` |
| `recall` | `fragment_top_k`, `fact_top_k`, `max_fragments_per_session`, `context_token_budget`, `expand_neighbor_count`, `timeout_ms` |
| `backfill` | `enabled`, `batch_size` |
| `features` | `archive_pipeline`, `auto_recall_per_turn`, `distill_on_close`, `profile_compile`, `backfill` |

功能开关环境变量使用 **`ASSISTANT_CHAT_MEMORY_FEATURES_*`**（规范名）；旧版 `ASSISTANT_CHAT_MEMORY_FEATURE_*`（单数）仍兼容，若两者同时设置则以 `FEATURES_*` 为准。

默认**全部功能关闭**，灰度按 team 开启。遗留 `ASSISTANT_MEMORY_ENABLED=true` 仍可通过 `resolve_effective_memory_enabled()` 视为记忆总开（用于仪表盘兼容）；**默认值为 `false`**，且**不会**单独触发关闭后归档流水线（须显式开启 `chat_memory.archive.enabled` 或 `features.archive_pipeline`）。

### 10.1 Web 后台可配置项（热生效）

以下项可在 **Pulse 管理后台 → 设置 → 集成与 LLM** 中编辑，写入 `team_settings` 表；**保存后下一请求/任务即生效**，无需重启 assistant 或 pulse channel（与 `assistant_llm` 相同机制）。

| 分区 | 配置键 | UI 标签 | 类型 | 默认值 | 归属 | 热生效机制 |
|------|--------|---------|------|--------|------|------------|
| `chat_memory.archive` | `enabled` | 启用永久归档 | bool | `false` | Assistant | `resolve_effective_chat_memory()` 每次从 DB 读取 |
| | `index_version` | 索引版本 | int | `2` | Assistant | 同上 |
| | `ledger_retention_days` | 运行账本保留天数 | int | `180` | Assistant | 同上 |
| `chat_memory.features` | `archive_pipeline` | 关闭后归档流水线 | bool | `false` | Assistant | 同上 |
| | `auto_recall_per_turn` | 每回合自动召回 | bool | `false` | Assistant | 同上 |
| | `distill_on_close` | 关闭时提炼摘要/事实 | bool | `false` | Assistant | 同上 |
| | `profile_compile` | 私聊交互画像编译 | bool | `false` | Assistant | 同上 |
| | `backfill` | 允许历史回填 | bool | `false` | Assistant | 同上 |
| `chat_memory.recall` | `fragment_top_k` | 片段 Top-K | int | `3` | Assistant | 同上 |
| | `fact_top_k` | 事实 Top-K | int | `5` | Assistant | 同上 |
| | `max_fragments_per_session` | 单会话片段上限 | int | `2` | Assistant | 同上 |
| | `context_token_budget` | 上下文 Token 预算 | int | `1500` | Assistant | 同上 |
| | `expand_neighbor_count` | 展开相邻片段数 | int | `2` | Assistant | 同上 |
| | `timeout_ms` | 召回超时（毫秒） | int | `500` | Assistant | 同上 |
| | `fts_weight` / `vector_weight` | FTS / 向量权重 | float | `0.5` | Assistant | 同上 |
| `chat_memory.chunking` | `max_tokens_per_chunk` | 分块最大 Token | int | `512` | Assistant | 同上 |
| | `overlap_tokens` | 分块重叠 Token | int | `64` | Assistant | 同上 |
| `chat_memory.embedding` | `enabled` | 启用向量嵌入 | bool | `true` | Assistant | 同上 |
| | `model` | 嵌入模型 | string | `text-embedding-3-small` | Assistant | 同上 |
| | `batch_size` | 嵌入批大小 | int | `32` | Assistant | 同上 |
| | `dedupe_by_content_hash` | 按内容哈希去重 | bool | `true` | Assistant | 同上 |

**UI 分组说明**（管理后台 → 设置 → 集成与 LLM）：

- **聊天记忆**：单一弹窗，渐进式披露——先开「启用永久归档」，再按需展开流水线、每回合召回、历史回填与高级分块参数；`embedding.enabled` / `embedding.model` 仍在 **「助手 LLM（对话）」** 中（与对话模型共用 API Key / Base URL）；`embedding.batch_size` 与 `dedupe_by_content_hash` 在聊天记忆弹窗「高级 · 分块与嵌入批处理」区。后端存储键不变，仍写入 `team_settings.chat_memory.*`。
- **联网搜索（Tavily）**：独立弹窗；开启总开关后显示 Key、超时、结果数与抓取限制。
| `chat_memory.backfill` | `enabled` | 启用回填任务 | bool | `false` | Assistant | 同上 |
| | `batch_size` | 回填批大小 | int | `20` | Assistant | 同上 |
| `web_search` | `enabled` | 启用联网搜索 | bool | `false` | Pulse | `effective_config()` 每次 capability 调用前合并 |
| | `api_key` | Tavily API Key（掩码） | secret | 空 | Pulse | 同上；可 reveal，不入日志 |
| | `timeout_seconds` | 搜索超时（秒） | float | `10` | Pulse | 同上 |
| | `max_results` | 默认最大结果数 | int | `5` | Pulse | 同上 |
| | `rate_limit_per_minute` | 每分钟速率限制 | int | `30` | Pulse | 同上 |
| | `fetch_max_bytes` | 网页抓取最大字节 | int | `1048576` | Pulse | 同上 |
| | `fetch_max_redirects` | 最大重定向次数 | int | `5` | Pulse | 同上 |

**仍仅环境变量 / Secret Store 的配置**（有意不暴露 Web UI）：

| 项 | 原因 |
|----|------|
| `TAVILY_SEARCH_URL` / `web_search.search_url` | 提供商 endpoint 极少变更，避免误配 |
| `web_search.provider` | 首版固定 Tavily |
| `ASSISTANT_*` 服务地址、DB URL、`service_token` | 部署级基础设施 |
| `PULSE_INTERNAL_TOKEN` 等内部鉴权 | 安全边界 |

兼容：旧版在 `assistant_llm.chat_memory` 下的嵌套 override 仍有效；独立 `chat_memory` 分区优先级更高。

---

## 11. 阶段验收门

| 阶段 | 验收 |
|------|------|
| 1–2 | 可删除、可回填、可验证的永久档案；尚不影响模型回答 |
| 3 | 关闭会话生成带证据摘要、事实与画像候选 |
| 4 | 每回合召回 + 渐进展开；通过隐私隔离与删除验收 |
| 5 | Tavily 经 capability 可用；引用/隐私/SSRF 验收 |
| 6–7 | 用户控制 API、测试与灰度观测完成后默认开启 |

---

## 12. 灰度启用与上线步骤

1. **单团队试点**：在管理后台 **设置 → 集成与 LLM → 聊天记忆 · 归档与功能** 开启归档与 `archive_pipeline`；或继续用环境变量。保持 `auto_recall_per_turn` 关闭，先验证关闭会话后 2 分钟内 `ready` 比例。
2. **历史回填**：试点团队开启 `features.backfill` 并执行 backfill，校验 FTS/向量计数与 `chunk_total` 一致后，再开启 `auto_recall_per_turn`。
3. **召回与工具**：观察日志字段 `event=recall_bundle`（来源、命中数、token_estimate）、`event=memory_tool`（expand 次数）；确认日志**不含**正文与搜索词。
4. **联网**：在 **联网搜索（Tavily）** 配置 Key 并启用；或设置 `WEB_SEARCH_ENABLED` + `TAVILY_API_KEY`。监控 `event=web_search` 的 `status` / `error_code`。
5. **全量默认**：试点达标（召回 P95 ≤ 500ms、归档成功率 ≥ 99.5%）后，通过环境变量或全局配置默认开启；未 opt-out 用户自动受益。

**Web 后台快速启用（推荐试点团队）**：

| 步骤 | 后台入口 | 建议值 |
|------|----------|--------|
| 1 | 聊天记忆 · 归档与功能 → 启用永久归档 | 开 |
| 2 | 同上 → 关闭后归档流水线 | 开 |
| 3 | 回填完成后 → 每回合自动召回 | 开 |
| 4 | 私聊场景 → 私聊交互画像编译 | 按需开 |
| 5 | 联网搜索（Tavily）→ 启用 + API Key | 按需 |

**环境变量快速参考**（全局默认或 CI；可被 team override 覆盖）：

| 变量 | 试点建议 |
|------|----------|
| `ASSISTANT_CHAT_MEMORY_ARCHIVE_ENABLED` | `true` |
| `ASSISTANT_CHAT_MEMORY_FEATURES_ARCHIVE_PIPELINE` | `true` |
| `ASSISTANT_CHAT_MEMORY_FEATURES_AUTO_RECALL_PER_TURN` | 回填后 `true` |
| `ASSISTANT_CHAT_MEMORY_FEATURES_PROFILE_COMPILE` | `true`（私聊） |
| `ASSISTANT_MEMORY_ENABLED` | 默认 `false`；`true` 仅兼容旧总开关（不触发归档流水线） |
| `WEB_SEARCH_ENABLED` | 按需 `true` |
| `TAVILY_API_KEY` | Pulse Secret Store / 环境变量 |
