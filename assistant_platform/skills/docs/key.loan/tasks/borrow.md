---
name: 借用临时 Key
summary: 额度不足且已绑定 Key 时，借用团队富余账号的临时 Cursor Key。
audience: [member]
when_to_use:
  - 用户说「借 Key」「临时 Key」「额度不够想继续写」
---

## 借用临时 Key

用户额度偏紧且已绑定 Key 时，调用 tool `key_loan_request`。

借用成功后告知有效期与使用注意；提醒可在额度恢复或不再需要时归还。

### 展示版式（按 tool `result` 排版，禁止编造）

tool 成功时 `user_message` 为空；只读 `result`（`schema_version=1`）。

1. 私聊须完整展示 `result.api_key`（禁止掩码）；群聊勿输出明文 Key。
2. 说明借出人 `lender_name`、警告/注意 `warning`（若有）。
3. 提醒可在不需要时「归还 Key」。
4. 失败时才向用户说明 error/`user_message`。
