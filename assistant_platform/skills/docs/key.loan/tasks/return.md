---
name: 归还 Key
summary: 归还不再需要的临时借用 Key。
audience: [member]
when_to_use:
  - 用户说「归还 Key」或不再需要临时 Key
---

## 归还 Key

用户说「归还 Key」或不再需要临时 Key 时，调用 tool `key_loan_return`。

归还成功后确认已释放借用记录。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`（含 `schema_version` 或 `text`）。

1. 用一两句确认已归还；可参考 `text`，勿编造。
2. 失败时才说明 error/`user_message`。
