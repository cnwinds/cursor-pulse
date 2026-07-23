# Bot 命令全量切流至 Assistant Platform

**Goal:** 钉钉/Web 文本经 Assistant 编排回复；Pulse channel 仅为 Channel Adapter。

**Architecture:** 文本一律镜像；Orchestrator `match_capability_intent` → CapabilityExecutor → Pulse Provider（`run_command` 薄封装）。Bot 本地仅处理文件/图片/「设置引导图」状态机。

**已移除开关：** `ASSISTANT_TAKEOVER`、`ASSISTANT_SHADOW_MODE`、`ASSISTANT_LEGACY_CHAT`。

## 切流开关

```
ASSISTANT_MIRROR_ENABLED=true
ASSISTANT_MIRROR_BASE_URL=http://127.0.0.1:8090
```

## 验收

1. 私聊「查询 我的用量」只回一条（Assistant）
2. Bot handler 对普通文本不调用 `reply_text`
3. 「设置引导图」仍本地进入 pending 上传态
