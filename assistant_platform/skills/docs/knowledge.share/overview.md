---
name: 团队技巧分享
summary: 分享 AI 使用心得；浏览团队技巧库。
audience: [member]
when_to_use:
  - 用户要分享技巧或浏览团队技巧库
---

# 团队技巧分享

**说法示例：**「我想分享一个 Cursor 技巧」「查看技巧库有哪些」

## 分享

1. 说清适用场景与可执行步骤
2. 助手整理 Markdown 草稿，用户确认后调用 `knowledge_tip_create`

## 浏览

调用 `knowledge_tip_list` / `knowledge_tip_read` 查看技巧库。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`。

1. **列表**：用 `entries` 的标题/摘要排版，勿编造未返回条目。
2. **详情**：用 `title` + `body`（Markdown）原样或轻度整理后展示。
3. **创建**：确认已收录；可回显标题。
4. 失败时才说明 error/`user_message`。
