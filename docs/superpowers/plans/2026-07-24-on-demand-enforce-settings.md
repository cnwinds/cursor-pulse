# On-Demand Enforce Settings Implementation Plan

> **For agentic workers:** Implement task-by-task. Steps use checkbox syntax.

**Goal:** Make On-Demand force-disable and DingTalk notify recipients configurable in Web settings (Cursor sync dialog).

**Architecture:** Add fields on `CursorSyncConfig`; resolve notify recipients from member ids (+ optional primary) with admin fallback; extend Settings UI with switches + member multi-select.

**Tech Stack:** Python/Pydantic, Vue 3 + Element Plus, existing `/api/settings` + `/api/v2/members`

---

### Task 1: Config + recipient resolution + sync wiring
### Task 2: Web-admin Settings UI
### Task 3: Tests
