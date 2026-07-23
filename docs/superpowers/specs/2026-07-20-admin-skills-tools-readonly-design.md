# 管理后台：Skills / Tools / Prompt 只读对齐设计

**日期：** 2026-07-20  
**状态：** 已落地（2026-07-20）；技能一览的「按 skill_id 展示」已随文件级 Skill 迁移调整，见下方 2026-07-21 注  
**范围：** Web Admin「助手中心」信息架构；Skill / Prompt 文件真源与只读预览；Capability 授权保留可写；Prompt 从 DB Release 回迁文件  
**前置：** [2026-07-20-dingtalk-skills-tools-design.md](./2026-07-20-dingtalk-skills-tools-design.md)、[2026-07-14-admin-console-cleanup-design.md](./2026-07-14-admin-console-cleanup-design.md)、[2026-07-14-assistant-platform-design.md](./2026-07-14-assistant-platform-design.md) §14

> **注（2026-07-21）：** Skill 真源已从「`catalog.yaml` 服务域卡片 + `docs/{skill_id}/**/*.md` 多文件」迁移为「**每个 `docs/**/*.md` 文件即一个独立 Skill**」，详见 [2026-07-21-file-as-skill-vector-routing-design.md](./2026-07-21-file-as-skill-vector-routing-design.md) §7.3。本文档 §4.1「技能一览」左卡片列表现按**文件**（非服务域）一行展示，`skill_id` 为文件相对路径（如 `cursor.self/tasks/quota`）；右侧 Markdown 直接展示该文件内容，不再有「分节」概念。其余只读对齐结论（内容真源为仓库文件、Tool 授权独立可写）不变。

## 1. 背景与问题

钉钉对话路径已重构为 **Skill（服务卡片/说明书）+ Tool（Capability 执行）**，`run_command` 已删除。管理后台仍停留在旧心智：

| 现状 | 问题 |
|------|------|
| 「能力中心」只展示扁平 Capability + assignments | 运营看不见用户侧的 Skill 服务域 |
| Prompt Studio 以 DB fragment/release 为可写运营面 | 与「说明书走仓库」不一致；且与 Skill 文档双轨 |
| Skill docs / catalog.yaml 仅在仓库 | 后台无法对照生效内容 |
| 设计稿曾将 Skill Studio 标为非目标 | 需要的是 **只读对齐**，不是在线 CMS |

产品目标：后台能 **看清** Skills / Tools / Prompt 三层；**改说明书与人设走仓库发版**；**改谁能调工具仍走后台授权**。

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 范围 | 助手中心对齐 Skills/Tools + Skill/Prompt 内容面（只读） |
| 内容真源 | **仓库文件**；不为 Skill/Prompt 内容建运营 DB |
| Skill ↔ Tool | **严格分离**：Skill 控制卡片/文档可见叙事；执行权只看 Capability assignment |
| 元数据覆盖 | **不做**按团队/全局 DB 覆盖；audience 等仍在 `catalog.yaml` |
| Prompt | **回迁文件**；后台只读；停用 DB 发布管线 |
| 可写后台 | 主要保留 **工具授权（assignments）** 及既有 Pulse 业务编辑 |

曾讨论后否决：Skill/Prompt 在线编辑、DB release/canary、Skill 联动改授权、按团队文案覆盖。

## 3. 目标与非目标

### 3.1 目标

1. 助手中心拆成 **技能一览 / 工具授权 / Prompt 一览**（会话账本保持）。
2. Skill、Prompt 经只读 API 展示与运行时相同的文件真源。
3. Prompt 运行时改为读 `assistant_platform/prompts/docs/`，不再依赖 `ap_prompt_releases` production。
4. 旧 Prompt 写 API（创建 fragment/release、canary、promote、rollback、approve）退役。
5. UI 文案明确：改技能/Prompt → 改仓库；改执行权 → 工具授权页。

### 3.2 非目标

- Skill Studio / Prompt CMS（草稿、发布、灰度、回滚）
- Skill 与 Capability assignment 自动联动
- 按团队覆盖 Skill/Prompt 文案
- 引导图、bot-commands 独立后台编辑器
- 本期删除 `ap_prompt_*` 物理表（可作为后续 chore；本期停写即可）

## 4. 信息架构与权限

### 4.1 菜单

| 菜单 | 路由 | 读 | 写 |
|------|------|----|----|
| 技能一览 | `/skills` | catalog + docs + help_topics | 无 |
| 工具授权 | `/capabilities`（改名/改文案） | catalog / packs / assignments / resolved | **仅 assignments** |
| Prompt 一览 | `/prompts`（替换 Prompt Studio 编辑页） | 文件片段 + 拼接预览 | 无 |
| 会话账本 | `/sessions` | 既有 | 既有 |

Pulse / 系统设置 / 用户权限等业务页不变。

### 4.2 权限码

- `assistant:skills:read`（新；无 write）
- `assistant:capabilities:read` / `assistant:capabilities:write`（write 仅 assignment）
- `assistant:prompts:read`（保留）；`assistant:prompts:write` 及 release/canary 类写权限废弃，旧角色映射为 read

## 5. 文件布局与运行时

### 5.1 Skill（已有，后台补只读面）

```
assistant_platform/skills/
  catalog.yaml
  help_topics.yaml
  docs/{skill_id}/overview.md
  docs/{skill_id}/tasks/*.md
  docs/{skill_id}/admin.md   # 可选
```

运行时继续 `SkillRegistry`。后台 API 复用同一加载逻辑，响应中附带仓库相对路径。

### 5.2 Prompt（DB/常量 → 文件）

```
assistant_platform/prompts/
  docs/heart.md
  docs/precepts.md
  manifest.yaml            # 片段顺序与说明
  loader.py                # 读盘 compose，替代 DB compose 路径
```

- 将现有 `CANONICAL_FRAGMENTS`（`prompts/fragments.py`）落到 Markdown 文件。
- Agent system 组装：**文件 loader + 既有 `agent_policy`（技能卡片等）**。
- 若启动时 DB 仍有 production 且与文件不一致：打 **warning**，**不以 DB 覆盖文件**。
- 停止向 `ap_prompt_*` seed/写入；表保留至后续清理 chore。

### 5.3 分层真源

| 层 | 真源 | 后台 |
|----|------|------|
| Skill 卡片/文档 | 仓库文件 | 只读 |
| Prompt 片段 | 仓库文件 | 只读 |
| Capability 定义 | `capabilities/catalog.py` | 目录只读 |
| Capability 授权 | DB assignments | 可写 |

## 6. API

经 Pulse `/api/v2/assistant/*` 代理（与现助手 API 一致）。

### 6.1 新建只读

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/skills` | 卡片列表 |
| GET | `/skills/{skill_id}` | 详情 + 分节 Markdown |
| GET | `/skills/help-topics` | topic 映射（可选） |
| GET | `/prompts` | 片段列表（key、路径、摘要） |
| GET | `/prompts/preview` | 按 manifest 拼接预览 |

内容类 **禁止** POST/PATCH/DELETE。

### 6.2 保留

`/capabilities/catalog|packs|assignments|members/{id}/resolved` — 行为不变；UI 改文案。

### 6.3 退役

现有 Prompt fragments POST、releases POST、canary / promote / rollback、proposals approve → 移除或返回 **410 Gone**。

## 7. 页面行为

**技能一览：** 左卡片（name / audience / aliases / `pending_hint`），右 Markdown 分节 + 仓库路径；顶栏提示「修改请改仓库并发版」。

**Prompt 一览：** 片段 Tab + 拼接预览；无草稿/发布/灰度/回滚。

**工具授权：** 现逻辑；可选弱展示「文档中提及的 tool 名」（不自动改 assignment）。

## 8. 迁移顺序

1. Prompt 文件落地 + runtime 改读盘；测试 Agent 组装不依赖 production release。  
2. Skills / Prompts 只读 API + 两个只读页；Capabilities 改名为工具授权。  
3. Prompt Studio 编辑 UI 下线；写 API 410。  
4. 停 seed 写 `ap_prompt_*`；DB≠文件时 warning。  
5. 更新相关旧设计文档中「Prompt 可写 / Skill Studio」表述；`ap_prompt_*` 删表另开 chore。

## 9. 错误处理与测试

- 缺文件或 YAML 损坏：只读 API 返回明确错误；后台错误条；**不影响**工具授权页。  
- 测试覆盖：文件 loader / SkillRegistry；只读 API；assignments 可写；旧 prompt 写接口 410；对话路径 system prompt 来自文件。

## 10. 成功标准

1. 运营打开「技能一览」能看到与钉钉侧一致的约 8 张技能卡片及文档。  
2. 「工具授权」仍能增删 assignment，且与 Skill 开关无关。  
3. 「Prompt 一览」展示文件内容；无法在后台改 Prompt。  
4. 新部署无需 DB prompt release 即可对话；仓库改 `heart.md`/`precepts.md` 发版后生效。  
5. 旧 Prompt Studio 写路径不可用（410 或路由移除）。

## 11. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 环境曾改过 DB Prompt，与文件不一致 | warning + 文档要求人工 diff；不以 DB 覆盖 |
| 角色仍持有 prompts:write | 映射为 read；写 API 410 |
| 误以为后台能改技能文案 | UI 明确「仓库发版」 |

---

**变更记录**

| 日期 | 说明 |
|------|------|
| 2026-07-20 | 定稿：文件真源、助手中心三栏只读对齐、Prompt 回迁文件、仅 Tool 授权可写 |
| 2026-07-20 | 已落地：Prompt 文件 loader、Skills/Prompts 只读 API、web-admin 三栏 UI、写接口 410 |
