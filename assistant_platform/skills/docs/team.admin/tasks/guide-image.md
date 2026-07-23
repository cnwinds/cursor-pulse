---
name: 设置引导图
summary: 管理员设置或更新绑定 Key、借 Key 流程的引导图。
audience: [admin]
when_to_use:
  - 管理员要设置或更新引导图
---

## 设置引导图

**说法：** `设置引导图`

调用 tool `guide_image_update`（或通过 Channel 本地：发送命令后 **下一条消息** 须为图片）。

用于更新绑定 Key、借 Key 流程配图。

### 展示版式（按 tool `result` 排版）

tool 成功时 `user_message` 为空；只读 `result`。

1. 确认引导图已更新（可参考 `updated` / `image_path`）。
2. 失败时才说明 error/`user_message`。
