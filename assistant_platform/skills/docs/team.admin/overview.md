---
name: 团队运营管理
summary: 成员管理、提交进度、引导图等 Cursor 运营操作。
audience: [admin]
when_to_use:
  - 管理员想了解团队运营管理功能总览/入口
---

# 团队运营管理

管理员运营类操作集中在此技能；各任务分节按需 `load_skill_docs(..., section=steps)` 查看。

用量采集仅支持 **Cursor API Key 自动同步**。月报、聚合、CSV 导出、告警等能力已移除；请使用 Web 管理后台查看额度与同步状态。

## 任务索引

| 说法 | tool |
|------|------|
| 状态 / `/status` | `submission_status_read` |
| 成员 … | `members_manage` |
| 设置引导图 | `guide_image_update` |

## Web 管理（推荐）

| 功能 | 入口 |
|------|------|
| 台账 / 绑 Key / 同步 | 账号台账 |
| 额度 / 借用 | 额度看板、借用记录 |
| Proxy Key | 代理 Key |
| 用户权限 | 用户与权限 |

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
