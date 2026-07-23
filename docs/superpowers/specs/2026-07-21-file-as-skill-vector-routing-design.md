# 文件即 Skill + 向量路由名片设计

**日期：** 2026-07-21  
**状态：** 已落地（2026-07-21）  
**范围：** Skill 真源与卡片模型；每轮 system 注入；`load_skill_docs`；Skill 向量索引与自动重建；管理后台技能一览；`help_topics` 迁移  
**前置：** [2026-07-20-dingtalk-skills-tools-design.md](./2026-07-20-dingtalk-skills-tools-design.md)、[2026-07-20-tool-data-skill-presentation-design.md](./2026-07-20-tool-data-skill-presentation-design.md)、[2026-07-20-admin-skills-tools-readonly-design.md](./2026-07-20-admin-skills-tools-readonly-design.md)  
**非目标：** Skill↔Tool 系统级绑定；词法 aliases 打分引擎；在线 CMS 编辑 Skill

---

## 1. 背景与动机

当前 Skill 有两层身份：

1. `catalog.yaml` 中的服务域卡片（约 9 张，`skill_id` 如 `cursor.self`）
2. `docs/{skill_id}/**/*.md` 多文件说明书

每轮将**全部可见卡片**注入 system，再靠 `load_skill_docs(skill_id, section?)` 合并多文件。问题：

- 卡片层与文件层重复；改文档还要同步 catalog
- 起始上下文随卡片/场景文案变长，且与「陪聊」场景无关时仍全量注入
- 向量路由若按「文件夹 skill」聚合，和「一文件一说明书」心智不一致

目标：**拆掉 catalog**；**每个 Markdown 文件 = 一个独立 Skill**（自带名片）；用**向量语义检索**决定本轮注入哪些名片；0 命中不注入（可陪聊）；模型按需 `load_skill_docs` 读该文件正文。

---

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 真源 | 仅 `assistant_platform/skills/docs/**/*.md`；删除运行时对 `catalog.yaml` 的依赖 |
| 粒度 | **一文件一 Skill**，不合并多文件为一个 skill |
| 名片 | 来自该文件 YAML frontmatter（+ 一级标题作 name 回退） |
| 路由 | 用户本轮文本 → embed → 在可见 Skill 向量中检索 → score 过滤 → 注入命中名片 |
| 0 命中 | **不注入任何技能名片**（陪聊/通用回复） |
| 渐进加载 | 命中名片后，模型自行决定是否 `load_skill_docs(skill_id)` |
| 索引维护 | 文档变更**自动**重建（按文件 content hash）；启动校验 + 运行期扫描/监听 |
| Tool / 授权 | 不变；Skill 仍只 prose 提及 tool 名 |

---

## 3. 核心模型

### 3.1 Skill 文件

路径示例（目录仅作组织，**不再**是 skill 身份）：

```text
assistant_platform/skills/docs/
  cursor.self/tasks/my-usage.md    → skill_id: cursor.self/tasks/my-usage
  cursor.self/tasks/quota.md       → skill_id: cursor.self/tasks/quota
  bot.guide/overview.md            → skill_id: bot.guide/overview
  key.loan/admin.md                → skill_id: key.loan/admin
```

**`skill_id`：** 相对 `docs/` 的 POSIX 路径，去掉 `.md` 后缀。  
改名/挪文件 = breaking change（发版约定）；若需稳定别名，可在 frontmatter 增加可选 `skill_id` 显式覆盖（默认仍用路径）。

### 3.2 Frontmatter（名片 schema）

每个文件须可解析为名片。推荐字段：

```yaml
---
name: 我的用量                    # 可选；缺省取正文第一个 # 标题
summary: 查看本人 Cursor 用量明细  # 可选；缺省可用 when_to_use 首条或标题
when_to_use:
  - 用户问「我的用量」或查本人 Cursor 用量明细
audience: [member]               # 必填；兼容「适用场景」中文键作 when_to_use
aliases: [我的用量, my_usage]     # 可选；写入嵌入文本，不另做词法引擎
privacy: private                 # 可选
pending_hint: false              # 可选；为 true 且 actor 为主管且有待审时，名片 summary/when_to_use 追加动态提示（逻辑同现 registry）
---
```

**名片注入内容（短）：** `skill_id`、`name`、`summary`、`when_to_use`（建议最多 3 条）、`privacy`（若有）。  
**正文：** frontmatter 之后的 Markdown；`load_skill_docs` 返回时仍注入「**适用场景**」列表（与现逻辑一致），便于模型对照。

### 3.3 废弃

| 废弃 | 说明 |
|------|------|
| 运行时 `catalog.yaml` | 迁移完成后删除或改为生成物/文档附录（非真源） |
| `load_skill_docs` 的 `section` 合并多文件 | 改为加载**单个** skill 文件；`section` 参数 deprecate（**忽略**，保持调用兼容） |
| 「服务域文件夹 = 一张卡」 | 后台与 Agent 均改为文件级 |

---

## 4. 向量索引

### 4.1 索引单元

**每个 Skill 文件 → 一条主向量**（文件不太长时整篇嵌入）。  
若单文件超过嵌入上限，按标题切开为多 chunk，元数据均带同一 `skill_id`；检索时对该 `skill_id` 取 **max(score)** 再参与 top‑k（**不**跨不同 skill_id 合并）。

嵌入文本建议：

```text
name / skill_id / summary / when_to_use / aliases
---
正文（可截断至模型上下文安全长度）
```

### 4.2 存储

Assistant DB 独立表（名称示例：`ap_skill_embeddings`），与会话记忆/archive 向量**分表**：

| 列 | 说明 |
|----|------|
| skill_id | PK |
| rel_path | `docs` 下相对路径（含 `.md`） |
| content_hash | 文件字节 hash |
| audience_json | 冗余，便于过滤 |
| embedding | JSON/blob，维度与现网 embedder 一致 |
| updated_at | |

复用现有 `Embedder` / OpenAI 兼容 embeddings 客户端（与 archive 同源配置），**索引生命周期独立**。

### 4.3 自动重建

| 时机 | 行为 |
|------|------|
| 进程启动 | 扫描 `docs/**/*.md`，hash 与表比对；新增/变更 → 重嵌；删除 → 删行 |
| 运行中 | 周期扫描（默认 30–60s）或 `watchdog` 监听；单文件粒度更新 |
| 嵌入失败 | 打 error 日志；该文件本轮不可检索；不阻断对话 |

配置项（示例）：`skills.vector.enabled`、`skills.vector.score_threshold`、`skills.vector.top_k`、`skills.vector.resync_interval_seconds`。

---

## 5. 每轮路由与注入

```text
1. visible = 扫描/注册表中 audience ∩ actor.audiences 的全部 Skill
2. 若 skills.vector.enabled：
     q = embed(本轮用户文本)
     hits = 在 visible 的向量中检索，score ≥ threshold，取 top_k
     cards = hits 对应名片
   否则（开关关闭/嵌入不可用）：
     回退策略 = 不注入名片（与 0 命中一致），并打 warning
     （不在一期做「全量文件名片」回退，避免卡片膨胀）
3. build_agent_system(..., skill_cards=cards)
4. 模型可 load_skill_docs(skill_id) 读正文后调 Tool
```

**0 命中 / 关闭向量：** system **不含**「可用技能」名片块（或仅保留一句：`当前未匹配到专项技能；可陪聊，或用户明确任务后再检索`）。  
不注入极简目录、不全量回退。

**Audience：** 检索前过滤不可见 skill；`manager`/`admin` 文件仅对具备对应 audience 的 actor 可见（沿用现 `SkillActorContext.audiences`）。

---

## 6. `load_skill_docs`

| 参数 | 说明 |
|------|------|
| skill_id | 文件级 id（如 `cursor.self/tasks/my-usage`） |
| section | **废弃**；若传入则忽略（不报错） |

- 本地执行；校验 audience；返回该文件渲染后的 Markdown（含适用场景块）
- 超长仍按 token budget 截断
- 不可见 / 不存在 → 结构化错误

Policy 文案更新：不确定细节时 `load_skill_docs(skill_id)`；展示版式以**该文件**内「展示版式」为准。

---

## 7. 迁移

### 7.1 文档

- 为每个现有 md 补齐 `name` / `summary`（若缺）
- 原「域 overview」：改为真正的导览型 skill（when_to_use 收窄为「总览/入口」），或拆内容进 tasks 后删除冗余 overview
- 去掉「多任务挤在一个 overview」的依赖；跨任务约束写进各文件正文

### 7.2 `help_topics.yaml`

`skill_id` 改为文件级 id，例如：

```yaml
- skill_id: cursor.self/tasks/quota
  topic_key: quota
  label: 额度
  aliases: [quota, 额度, 我的额度]
```

### 7.3 管理后台「技能一览」

- 左边：每个 skill **文件**一行（name、skill_id、audience）
- 右边：直接展示该文件内容（frontmatter 结构化 + 正文 Markdown）
- 提示文案：真源为 `assistant_platform/skills/docs/**/*.md`

### 7.4 代码触点（实现期）

- `SkillRegistry`：由 catalog 驱动改为 docs 扫描；`list_cards` / `load_docs` / 名片解析
- `orchestrator`：注入前走向量路由
- `agent_tools`：`load_skill_docs` schema
- `formatting.format_skill_cards_block`：兼容空列表
- 测试：registry、policy、admin API、路由单测（可用 HashingEmbedder）

### 7.5 `catalog.yaml`

迁移完成后自仓库删除；设计文档中旧「服务域 skill_id」表述以本文件为准。

---

## 8. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 短问召回失败（如「额度」） | 正文/when_to_use/aliases 写入嵌入文本；调低 threshold；观察日志 hit rate |
| 陪聊误召回业务卡 | 提高 threshold；top_k 小（建议默认 2～3） |
| 嵌入服务不可用 | 不注入名片 + warning；对话不中断 |
| 文件改名断引用 | 发版检查；help_topics / 文档互链用 skill_id |
| 卡片变多 | 正是向量路由存在的理由；禁止全量注入回退 |

---

## 9. 成功标准

1. 运行时不再读取 `catalog.yaml`。  
2. 问「我的用量」类请求：日志可见 skill 向量命中 `…/my-usage`（或等价 id），system 含该名片；模型可 `load_skill_docs` 后调 `usage_self_read`。  
3. 纯陪聊（无业务意图）：0 命中，system 无技能名片块，仍能正常回复。  
4. 修改任意 skill md 后，在 resync 间隔内索引 hash 更新，新语义可被检索到。  
5. 管理后台技能一览按**文件**列出并展示内容。  
6. Tool 授权与 invoke 行为不变。

---

## 10. 实现分期（建议）

| 阶段 | 内容 |
|------|------|
| P0 | Frontmatter schema + Registry 改扫盘；`load_skill_docs` 单文件；删 catalog 依赖；测通 |
| P1 | `ap_skill_embeddings` + 启动/周期重建 + 每轮向量路由注入 |
| P2 | help_topics / 后台一览 / policy 文案 / 旧设计文档修订 |

---

## 11. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-07-21 | 初稿：文件即 Skill；向量路由名片；0 命中不注入；自动重建索引；废除 catalog |
| 2026-07-21 | 已落地：Task 1–7 完成（扫盘 Registry、frontmatter 迁移、单文件 `load_skill_docs`、`SkillVectorIndex` + `ap_skill_embeddings`、orchestrator 路由接入、管理后台文件级 API/UI）；相关 pytest 套件（registry/vector/agent 集成/help/admin API/policy/help filter/orchestrator）全量通过；旧设计文档已加指向本文的说明 |
