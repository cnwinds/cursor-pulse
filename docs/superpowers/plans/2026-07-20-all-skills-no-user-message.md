# Tool 全量去 user_message / Skill 排版改造计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 所有 Capability **成功路径**不再依赖 `user_message`；结构化 `result`（含 `schema_version`）为唯一数据源，由 LLM 按 Skill「展示版式」排版。失败路径仍可用 `user_message` 说明错误。

**Architecture:** 对齐试点 `usage.self.read` / `key.loan.self.read`。`_success` 统一 `user_message=""`；`result` 带 `schema_version`。清空 `VERBATIM_PRIVATE_CAPABILITIES`（含 Key：私聊 policy 要求完整展示 `result.api_key`）。Channel 口令路径继续走 ops 字符串 formatter，不受影响。

**Tech Stack:** Python handlers、`CapabilityInvokeResult`、Skill Markdown frontmatter + 展示版式、pytest。

---

### Task 1: 协议与基础设施

**Files:**
- Modify: `pulse/capabilities/handlers/common.py`
- Modify: `assistant_platform/conversation/agent_tools.py`
- Modify: `docs/superpowers/specs/2026-07-20-tool-data-skill-presentation-design.md`
- Test: `tests/assistant_platform/test_agent_runtime.py`

- [x] `_success(reply, ...)` → `user_message=""`，`result={schema_version:1, capability_key, text: reply, **extra}`（过渡：`text` 保留原 Markdown，便于尚未结构化的能力）
- [x] `VERBATIM_PRIVATE_CAPABILITIES = frozenset()`
- [x] 更新 design：全量成功路径禁 `user_message`；失败保留

### Task 2: 读类能力结构化（优先）

**Files:** quota_self_read, submission_*, web_*, knowledge_tip_*, usage_query, bot.help

- [x] 抽出/复用 payload builder；成功 `user_message=""`
- [x] Skill 文档补「展示版式」
- [x] 单测断言 `result` + 空 `user_message`

### Task 3: Key / 借用写操作

**Files:** key_loan request/return/list/revoke, cursor_key_bind/unbind, access_request_*

- [x] request/bind：`result` 含完整 `api_key`（私聊）
- [x] return/revoke/list/decide：结构化字段
- [x] 更新 skill docs；清 verbatim 相关测试

### Task 4: 管理与其它写操作

**Files:** text_capabilities 管理类、guide_image、usage.manual.submit、aggregate/export/report/alerts/members/ingestion

- [x] 成功路径走 `_success` 新语义（已自动空 user_message + text）
- [x] 逐步把 `text` 换成结构化字段（本阶段允许 text 过渡）
- [x] team.admin / usage.other 文档补版式

### Task 5: 回归

- [x] `pytest` 相关 suites
- [x] 确认 Agent 对成功 tool 不再 verbatim；LLM 第二轮排版

---

**变更记录**

| 日期 | 说明 |
|------|------|
| 2026-07-20 | 初稿：全量去成功 user_message |
