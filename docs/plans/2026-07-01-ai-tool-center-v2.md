# AI 工具管理中心 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Pulse 从 Cursor 用量收集升级为 AI 工具管理中心：多厂家元数据、账号台账、主使用人、账号维度用量与催办。

**Architecture:** 在现有 Python 单体上扩展 `pulse/tool_center/` 模块；SQLAlchemy 新增 v2 表；催办与汇总以 `ai_accounts` 为粒度；CSV 解析与聚合引擎复用；不兼容 v0.4 成员维度催办（有账号台账后走账号逻辑）。

**Tech Stack:** Python 3.11+, SQLAlchemy 2, FastAPI (web), Vue 3 (admin), pytest

**Spec:** [PRD v2](../PRD-v2-ai-tool-center.md)

---

## 文件地图

| 路径 | 职责 |
|------|------|
| `pulse/storage/models.py` | v2 ORM：vendors/plans/accounts/summaries |
| `pulse/storage/migrate.py` | 轻量 ALTER + create_all |
| `pulse/tool_center/repository.py` | 台账 CRUD、缺报账号查询 |
| `pulse/tool_center/usage.py` | 用量汇总、额度比例、模型族聚合 |
| `pulse/tool_center/seed.py` | 预置厂家/套餐/试用池 |
| `pulse/tool_center/reminders.py` | 主使用人/管理员催办目标解析 |
| `pulse/storage/repository.py` | submission 绑定 account_id + 写 summary |
| `pulse/channels/reminders/scheduler.py` | 账号维度每日催办与截止提醒 |
| `pulse/web/accounts_api.py` | 台账 REST（P0 最小） |
| `tests/test_tool_center.py` | 台账、汇总、催办逻辑 |

---

## Phase P0 — 数据模型 + 台账 + 主使用人催办 ✅

### Task 1: v2 数据模型

- [x] `AiVendor`, `AiPlan`, `AiAccount`, `AiAccountMember`, `UsageSummary`
- [x] `Member` 扩展：`department_name`, `manager_dingtalk_user_id`, `manager_member_id`, `employment_status`
- [x] `Submission` 扩展：`account_id`, `vendor_id`
- [x] `migrate_schema` 补齐新列与新表

### Task 2: 种子数据

- [x] `pulse init-v2 --seed`：Cursor/智谱/MiniMax/Codex 厂商
- [x] Cursor Pro+（quota_denominator=70）及 Pro/Ultra
- [x] 3 个试用共享账号占位

### Task 3: 台账仓储与用量汇总

- [x] `ToolCenterRepository`：list_active_accounts, get_unsubmitted_accounts
- [x] `build_usage_summary(submission, plan)` → ratio + breakdown_by_model
- [x] 测试：Pro+ $66.5 → 95%

### Task 4: 提交与催办改造

- [x] `save_submission(account_id=...)` 按账号覆盖账期
- [x] 催办：有主使用人 → 私聊主使用人；无主使用人 → 私聊管理员
- [x] 截止群提醒：「N 个账号待上报」，不点名个人
- [x] 测试催办目标解析

### Task 5: Web API 最小台账

- [x] `GET/POST/PATCH /api/v2/accounts`
- [x] `GET /api/v2/vendors`, `GET /api/v2/plans`

**P0 DoD:** `pytest tests/test_tool_center.py` 全绿；`pulse init-v2 --seed` 可跑；催办逻辑单元测试通过。

---

## Phase P1 — 额度升级建议 + 聚合扩展 ✅

- [x] 连续 2 月 ≥95% → `suggest_dedicated`
- [x] 聚合 snapshot 含账号维度指标
- [x] 主管/管理员私聊简报（规则模板）
- [x] 群内匿名 digest
- [x] Web 账号台账页面

---

## Phase P2 — 申请审批 + 钉钉通讯录 ✅

- [x] `access_requests` 表与 API
- [x] 主管审批流（Web + 钉钉「审批 通过/拒绝」）
- [x] 管理员分配试用池账号
- [x] `pulse sync-directory` / Web 同步通讯录

---

## Phase P3 — 知识库 + 心得收集 ✅

- [x] `knowledge_entries` ORM + `KnowledgeService`（规则/LLM 整理）
- [x] REST `/api/v2/knowledge` + 月精选 digest 发群
- [x] 钉钉 Bot「心得：…」收录 + 月报后自动发技巧精选
- [x] Web `技巧知识库` 页面
- [x] `tests/test_knowledge.py`

---

## Phase P4 — 多工具提交适配 ✅

- [x] `ManualUsageService` + `save_manual_submission` 手工录入
- [x] 智谱/MiniMax/Codex 厂商截图 Vision 提取
- [x] 钉钉「上报 智谱 85」Bot 命令
- [x] Web `POST /api/v2/accounts/{id}/usage/manual` + 台账页上报
- [x] `tests/test_manual_usage.py`
