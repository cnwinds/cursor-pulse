---
name: 团队运营管理
summary: 月报、聚合、提交进度、成员、告警、导出、引导图等管理操作。
audience: [admin]
when_to_use:
  - 管理员想了解团队运营管理功能总览/入口（具体状态/聚合/报告/成员/告警/导出/引导图见任务索引对应文档）
---

# 团队运营管理

管理员运营类操作集中在此技能；各任务分节按需 `load_skill_docs(..., section=steps)` 查看。

## 任务索引

| 说法 | tool |
|------|------|
| 状态 / `/status` | `submission_status_read` |
| 聚合 / `/aggregate` | `usage_aggregate` |
| 报告 / `/report` | `report_publish` |
| 成员 … | `members_manage` |
| 告警 / `/alerts` | `alerts_run` |
| 导出 / `/export` | `usage_export` |
| 设置引导图 | `guide_image_update` |

## 遗留说明

历史「待审 / 确认 / 拒绝」摄取流程已停用；新上报直接入库。仅处理历史遗留数据时参考旧文档，勿提示新上报需要审核。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或过渡字段 `text`）。

1. 优先用结构化字段排版；若仅有 `text`，可参考其内容但勿编造额外数字。
2. 失败时才向用户说明 tool 的 error/`user_message`。
3. 私聊若 `result` 含 `api_key`，须完整原样展示，禁止掩码。
