# 企业微信 Bot 扩展指南（桩）

当前代码提供 `pulse.bot.platforms.wecom.WeComMessenger` 桩类。

## 接入步骤（待实现）

1. 在企业微信管理后台创建应用，配置消息接收
2. 配置 `bot.name: wecom` 与 CorpID/Secret（需扩展 `config.yaml`）
3. 实现 `WeComMessenger` 三个核心方法（同飞书文档）
4. 实现 `pulse/bot/wecom/handler.py` 回调入口

MVP 生产环境请继续使用 **钉钉**。
