# 概览页重新设计（Dashboard Redesign）

日期：2026-07-28
状态：已获用户确认

## 背景与问题

当前概览页（`web-admin/src/views/DashboardView.vue`）只消费 `GET /api/dashboard/overview` 一个接口，展示内容停留在"账号同步进度"时代：4 个同步统计卡、一个大进度条、一个运行概览描述表。项目已长出额度看板、用量分析、借用记录、代理 Key、助手中心等能力，概览页无法回答用户最关心的两个问题：

1. 有什么需要我处理？（额度告急、同步失败、待审批）
2. 团队整体用得怎么样？（花费、tokens、趋势）

## 已确认的决策

| 决策点 | 结论 |
|---|---|
| 页面定位 | 混合式：顶部异常与待办，下部 KPI 与用量趋势 |
| 受众与权限 | 所有登录用户的首页，区块按权限自适应显隐 |
| 数据架构 | 后端聚合：扩展 `/api/dashboard/overview`，按请求用户权限裁剪区块 |

## 页面结构（自上而下）

### ① 需要关注（条件显示，无异常时整块不出现）

每条 = 图标 + 描述 + 数量 + 点击跳转，按权限显示：

- 额度告急：X 个账号已耗尽 / Y 个预警 → `/quota-board`
- 同步异常：同步失败 / 未配置凭证 / 未绑定负责人 → `/accounts`
- 待审批用户 X 个 → `/users`
- 集成未配置（IM/LLM 等）→ `/settings`

### ② KPI 卡片行（最多 6 张，按权限显示）

- 活跃账号（副文案：已同步 15/15 · 100%）
- 本账期花费 USD
- 本账期 Tokens（M 格式化）+ 事件数
- 当前借出数
- 活跃代理 Key（副文案：累计花费）
- 额度告急账号数（耗尽 + 预警）

统一抽 `StatCard` 组件。

### ③ 用量趋势图

近 14 天每日 tokens + 费用（ECharts 双轴），数据复用 usage-analytics 的 `series_by_day` builder。权限 `accounts:read`。

### ④ 底部双栏

- 左：额度风险 Top 5（账号 + 进度条 + 预计耗尽日，点击去 `/quota-board`），权限 `accounts:read`
- 右：最近动态（审计流水 10 条，后端已有中文 label），权限 `audit:read`

### 砍掉/收纳的旧内容

- 账号同步进度大进度条 → 收进"活跃账号"KPI 卡副文案
- "运行概览"描述表（账期/团队/工作群）→ 不再单列，集成健康状态进"需要关注"

## 后端设计

扩展 `GET /api/dashboard/overview`（`pulse/web/dashboard_api.py`），路径不变、只增不改字段，向后兼容。新增 `sections` 对象，**按请求用户权限裁剪，无权限的键不返回**：

| sections 键 | 权限 | 内容 | 数据来源（复用） |
|---|---|---|---|
| `quota` | `accounts:read` | exhausted/warning/healthy 计数 + 风险 Top5（account_id、status、quota_progress、projected_exhaustion_date、days_until_reset） | quota-board builder（`pulse/web/quota_api.py:95-186` 所用） |
| `usage` | `accounts:read` | 账期 cost_usd / tokens / event_count + 近 14 天 series_by_day | `pulse/tool_center/usage_analytics.py:124` builder |
| `loans` | `accounts:read` | active_count | loans count（`pulse/web/quota_api.py:204` 附近） |
| `proxy` | `proxy:read` | 活跃 Key 数、累计 tokens/cost | proxy 摘要（`pulse/proxy/service.py:744`） |
| `integrations` | `settings:read` | IM/LLM/DB 等配置健康摘要 | 现有 integrations builder（`dashboard_api.py:70-150`） |
| `recent_activity` | `audit:read` | 最近 10 条审计流水（中文 label） | audit 格式化（`pulse/web/audit.py:419`） |

要点：

- 接口认证从 `require_capability("settings:read")` 放宽为登录即可（`require_portal_user`），权限裁剪在 builder 内部按用户 capability 集合做。
- 每个 section 独立 try/except：单个 section 构建失败置为 `null` 并记日志，接口整体不 500，页面永远可开。
- 顶层旧字段（`period`、`summary`、`ingestion`、`sync_stats`、`pending_actions`）保留，但**同样按权限裁剪**（评审修正）：`summary` 仅 `settings:read` 可见，`ingestion`/`submission`/`sync_stats` 仅 `accounts:read` 可见，`pending_actions` 仍按 `admin:users` 裁剪。对内置角色（owner/operator/auditor 均持双权限）行为与旧版完全一致。
- `usage` section 的日期边界按团队时区取当天（`ZoneInfo(timezone_name)`），避免月初服务器时区与团队时区错位导致 section 静默降级。

## 前端设计

- 重写 `web-admin/src/views/DashboardView.vue`：一次请求 overview，按返回的 sections 键渲染区块。
- 新增 `web-admin/src/components/StatCard.vue`：label + value + 副文案 + 可选跳转。
- 抽 `web-admin/src/utils/echarts.ts`：把 `UsageAnalyticsView.vue:201-210` 的 echarts 按需注册挪为共享模块，Dashboard 与 UsageAnalytics 共用。
- 路由守卫：`/` 的 `meta.permission` 从 `settings:read` 放宽为登录即可（`web-admin/src/router/index.ts:142-155`）。
- 加载态 `el-skeleton`；页头加手动刷新按钮（不做轮询）。
- 接口整体失败 → 页面级错误提示 + 重试按钮；用户无任何区块权限 → 简洁空状态。

## 错误处理

| 场景 | 行为 |
|---|---|
| 某 section 构建抛异常 | 后端捕获，该键返回 null + 记日志，前端跳过该区块 |
| 用户无某区块权限 | 后端不返回该键，前端不渲染 |
| overview 接口整体失败 | 前端页面级错误 + 重试 |
| 所有区块均无权限 | 前端空状态（欢迎语 + 可用功能入口提示） |

## 测试

- 后端 pytest：扩展 `tests/` 中 dashboard overview 相关用例——
  - 不同权限用户（owner / 仅 accounts:read / 仅 proxy:read / 无数据权限）返回的 sections 键不同
  - 聚合字段正确性（quota 计数、usage 汇总、loans active_count）
  - 某 section 失败时返回 null 且接口不 500
- 前端无测试框架，不新增（遵循项目现状）。

## 明确不做（YAGNI）

- 助手中心数据进概览（assistant 接口为代理转发，未配置会 503，且无计数接口）
- 自动刷新 / 轮询 / WebSocket
- 趋势图时间范围切换器（固定近 14 天）
- 新建告警中心 / AlertLog API
- 前端测试
