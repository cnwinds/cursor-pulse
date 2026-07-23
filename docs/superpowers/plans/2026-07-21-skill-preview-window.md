# Skill 预览窗口 + 续读 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 命中 Skill 时注入正文前 200 行与行数元信息；`load_skill_docs` 支持 `start_line`/`max_lines` 续读。

**Architecture:** `SkillRegistry.load_docs` 按正文行窗口切片并返回元信息；`format_skill_cards_block` 渲染名片+预览；orchestrator 为命中卡批量 `load_docs(start_line=1,max_lines=200)` 后注入 system 与 context ledger。

**Tech Stack:** Python、现有 SkillRegistry / agent tools / pytest

---

### Task 1: Registry 行窗口 API

**Files:**
- Modify: `assistant_platform/skills/models.py`
- Modify: `assistant_platform/skills/registry.py`
- Test: `tests/assistant_platform/test_skill_registry.py`

- [ ] 扩展 `SkillDocResult` 行数字段
- [ ] `load_docs(..., start_line=1, max_lines=200)`
- [ ] 夹具长文档测续读

### Task 2: Tool + Formatting

**Files:**
- Modify: `assistant_platform/skills/agent_tools.py`
- Modify: `assistant_platform/skills/formatting.py`
- Test: `tests/assistant_platform/test_skill_agent_integration.py`、`test_agent_policy.py`

- [ ] tool schema 增加 `start_line`/`max_lines`；返回元信息
- [ ] `format_skill_cards_block` 接受 previews

### Task 3: Orchestrator / Policy / Context

**Files:**
- Modify: `assistant_platform/conversation/agent_policy.py`
- Modify: `assistant_platform/conversation/orchestrator.py`
- Test: 相关 policy / orchestrator 测试

- [ ] 命中后加载 preview 传入 `build_agent_system`
- [ ] context snapshot 带行数
- [ ] policy 文案更新

### Task 4: 回归

- [ ] `pytest tests/assistant_platform/test_skill_*.py tests/assistant_platform/test_agent_policy.py tests/assistant_platform/test_agent_trace.py -q`
