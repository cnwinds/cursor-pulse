# Web Admin Console 菜单收敛设计

> 版本：v1  
> 日期：2026-07-14  
> 状态：已确认，待实施  
> 关联：[assistant-platform-design](./2026-07-14-assistant-platform-design.md) §14 Assistant Console

## 1. 背景与目标

Assistant Platform Phase 0–5 落地后，`web-admin` 侧栏同时存在：

- Pulse 业务菜单（额度、申请、知识库等）
- 旧 personamem 管理页（记忆、原则、披露、自进化）
- 新助手中心（能力中心、会话账本、Prompt Studio）

目标：按新 Console 收敛导航与对应管理端入口，删除历史无用菜单与功能，使后台与总体设计一致。

## 2. 范围

### 2.1 做

- 重组侧栏为三组：**Pulse / 助手中心 / 系统**
- 删除旧管理页的菜单、路由与 Vue 页面
- 移除仅被旧页使用的管理端 API
- 修正重复的 `prompt-studio` 路由定义
- 同步调整相关权限断言与前端/后端测试

### 2.2 不做

- 不拆除 bot 侧 personamem / evolution 运行时（助手平台记忆迁移完成前保留）
- 不实现设计文档中尚未落地的「助手总览 / 用户画像 / 评测与发布」新页
- 不改动 Pulse 账号、额度、借 Key、用量等领域逻辑
- 不改变登录与权限模型骨架（仅收敛默认暴露的权限/菜单）

## 3. 目标导航

| 分组 | 菜单 | 路由 | 权限 |
|------|------|------|------|
| Pulse | 概览 | `/` | `settings:read` |
| Pulse | 账号台账 | `/accounts` | `accounts:read` |
| Pulse | 额度看板 | `/quota-board` | `accounts:read` |
| Pulse | 工具申请 | `/access-requests` | `requests:read` |
| Pulse | 技巧知识库 | `/tool-tips` | `knowledge:read` |
| Pulse | 摄取记录 | `/ingestions` | `submissions:read` |
| 助手中心 | 能力中心 | `/capabilities` | `assistant:capabilities:read` |
| 助手中心 | 会话账本 | `/sessions` | `assistant:sessions:read:self` 或 `…:all` |
| 助手中心 | Prompt Studio | `/prompt-studio` | `assistant:prompts:read` |
| 系统 | 审计 | `/audit` | `audit:read` |
| 系统 | 系统（集成） | `/integrations` | `settings:read` |
| 系统 | 配置 | `/settings` | `settings:read` |
| 系统 | 用户管理 | `/users` | `admin:users` |

侧栏使用 `el-sub-menu` 或分组标题展示上述三组；仍按权限 `v-if` 控制可见性。

## 4. 删除清单

### 4.1 前端（菜单 + 路由 + 页面）

| 菜单 | 路径 | 视图文件 |
|------|------|----------|
| 指标 | `/metrics` | `MetricsView.vue` |
| 记忆 | `/memory` | `MemoryView.vue` |
| 原则 | `/principles` | `PrinciplesView.vue` |
| 披露 | `/disclosure` | `DisclosureView.vue` |
| 自进化 | `/evolution` | `EvolutionView.vue` |

另：删除 `router/index.ts` 中重复的 `prompt-studio` 路由条目。

### 4.2 后端管理 API

移除（或等价下线）仅服务上述旧页的端点：

- `GET /api/memory/atoms`
- `GET /api/memory/commitments`
- `GET /api/memory/principles`
- `POST /api/memory/principles`
- `GET /api/memory/disclosure`
- `GET /api/memory/evolution`
- `POST /api/memory/evolution/run`

保留：

- `GET /api/periods/{period}/metrics` 与导出相关接口（概览或其他 Pulse 流程可能仍依赖）
- bot / personamem 内部调用与数据表（不在本次拆除范围）

### 4.3 权限与测试

- 角色默认权限中不再需要为后台菜单暴露 `memory:read` / `memory:write` / `evolution:run`（若仍有内部代码检查可暂留常量定义，但不挂导航）
- 更新或删除 `tests/test_web_memory.py` 等针对已删管理 API 的测试
- 保留 `metrics:read` 权限（API 仍在），仅去掉「指标」页

## 5. 实现要点

1. **MainLayout.vue**：按三组重组菜单项；移除已删项的 icon 引用。
2. **router/index.ts**：删除对应 children；去重 `prompt-studio`。
3. **pulse/web/app.py**：删除 §4.2 所列路由注册。
4. **权限表 / seed**：若有显式菜单权限清单，同步收敛；避免破坏现有登录与角色枚举。
5. **验收**：登录后侧栏仅见 §3 清单中有权限的项；访问已删路径应 404 或落入无匹配路由；旧 memory 管理 API 返回 404。

## 6. 风险与回滚

- **风险**：运营仍依赖旧记忆/原则后台做人工干预 → 本次已确认这些页为历史无用，可删。
- **回滚**：Git 还原本次变更即可；bot 记忆数据与运行时未动。

## 7. 验收标准

- [ ] 侧栏分组为 Pulse / 助手中心 / 系统，无记忆/原则/披露/自进化/指标
- [ ] 概览、技巧知识库、摄取记录仍可访问
- [ ] 能力中心、会话账本、Prompt Studio 仍可访问
- [ ] 重复 `prompt-studio` 路由已消除
- [ ] 旧 `/api/memory/*` 管理端点不可用；相关测试通过或已移除
- [ ] Pulse 额度/申请/账号等业务不受影响
