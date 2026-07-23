---
name: 联网查资料
summary: 搜索公开网页并引用来源，补充实时信息。
audience: [member]
when_to_use:
  - 用户明确要求搜索、联网或核实时效性信息
---

# 联网查资料

用户明确要求搜索、联网或核实时效性信息时，调用 `web_search` / `web_fetch`。

- 搜索词仅来自当前用户消息，勿注入私人历史。
- 回答须引用来源与检索时间；失败须直说。
- 用户明确禁止联网时不得调用。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`。

1. 搜索：按 `results`/`answer`/`text` 归纳要点并附来源；勿编造链接。
2. 抓取：基于页面正文摘要回答用户问题。
3. 失败时才说明 error/`user_message`。
