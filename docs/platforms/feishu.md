# 飞书 Bot 扩展指南（桩）

当前代码提供 `pulse.channels.platforms.feishu.FeishuMessenger` 桩类。

## 接入步骤（待实现）

1. 在飞书开放平台创建企业自建应用，启用机器人
2. 配置 `bot.name: feishu` 与飞书 App ID/Secret（需扩展 `config.yaml`）
3. 实现 `FeishuMessenger` 的：
   - `send_group_text` — 群消息
   - `send_oto_text` — 私聊
   - `download_message_file` — 文件下载
4. 实现 `pulse/channels/feishu/handler.py` Stream/Webhook 入口，复用现有 `Repository` / 聚合逻辑

MVP 生产环境请继续使用 **钉钉**（`bot.name: dingtalk`）。
