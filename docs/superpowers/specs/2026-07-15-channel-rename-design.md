# Channel 命名对齐设计

**日期：** 2026-07-15  
**状态：** 已批准（对话确认）  
**范围：** 纯命名/路径重构，不改变业务行为、消息协议或 DB schema

## 背景

切流后 Pulse 侧职责是 **Channel Adapter + Capability Provider**，对话编排在 Assistant。开发进程与代码包仍叫 `bot`，与架构语义不符。本设计将我们自己的 bot 命名统一改为 channel。

## 目标

1. 开发服务名：`bot` → `channel`
2. CLI：正式命令 `pulse channel`；`pulse serve` 保留为 deprecated 别名
3. 包路径：`pulse/bot/` → `pulse/channels/`
4. 关键类型/函数：`BotMessenger` → `ChannelMessenger`，`PulseBotHandler` → `DingTalkChannelHandler`，`handle_bot_command` → `handle_channel_command`
5. 文档（含历史 plans/specs 正文）：对我们架构的 “bot / Pulse Bot” 叙述改为 channel / Channel Adapter
6. 历史计划**文件名**不改，避免外链断裂

## 硬排除（永不改名）

- 钉钉/SDK：`ChatbotHandler`、`ChatbotMessage`、`robot_code`、`DINGTALK_ROBOT_*`
- 钉钉 API 路径与响应字段中的 robot
- 第三方库符号

## 方案

采用**分层改名 + 硬排除第三方**（对话中方案 2）：

- 系统性改我们拥有的运行时名、包、符号、文档用语
- 不保留 `pulse.bot` 兼容 shim（一次切干净）
- `serve` 短时别名：仍启动同一入口，并提示改用 `channel`
- 开发服务只认 `channel`；不双写 `bot` pid。若本地仍有旧 `bot` pid，stop/status 时可提示已更名（可选，非必须）

## 符号映射

| 旧 | 新 |
|---|---|
| 开发服务 `bot` | `channel` |
| `.dev/pids/bot.json` / `logs/bot.log` | `channel.*` |
| CLI `pulse serve` | 正式：`pulse channel`；`serve` = deprecated 别名 |
| 包 `pulse/bot/` | `pulse/channels/` |
| `pulse.bot.*` | `pulse.channels.*` |
| `BotMessenger` | `ChannelMessenger` |
| `create_messenger` | 保留（工厂名，语义已是 messenger） |
| `PulseBotHandler` | `DingTalkChannelHandler` |
| `handlers/bot_command.py` | `handlers/channel_command.py` |
| `handle_bot_command` | `handle_channel_command` |
| 文案「Pulse Bot / bot 进程 / 钉钉机器人 + 调度」 | 「Channel Adapter / channel 进程 / 渠道适配 + 调度」 |

## 实施顺序

1. **运行时表层：** `pulse/dev/services.py`、`manager.py`、`cli.py`、`cursor-pulse.{bat,ps1,sh}`、`tests/test_dev_manager.py`
2. **包迁移：** `git mv pulse/bot pulse/channels`，全仓 import 与符号替换
3. **能力 handler：** `bot_command.py` → `channel_command.py`，更新 `invoke.py` 等引用
4. **文档扫尾：** README、RUNBOOK、platforms、superpowers plans/specs 正文；保留钉钉 robot 配置表述
5. **验收：** 见下节

## 不做

- 不改业务逻辑、消息协议、DB schema
- 不引入 `pulse.bot` 兼容包
- 不重命名历史计划文件名
- 不重命名钉钉/SDK 第三方标识

## 风险与回滚

- **半截 rename：** 按层连贯完成，避免 `pulse.bot` 与 `pulse.channels` 长期并存
- **本地旧 pid：** 改名后需重新 `stop` / `start`；文档一句说明即可
- **回滚：** revert 改名相关 commit

## 验收标准

- [x] `DEFAULT_SERVICES` / `SERVICES` 含 `channel`，不含 `bot`
- [x] `build_command("channel")` 成功；`build_command("bot")` 失败或明确拒绝
- [x] 无残留 `from pulse.bot` / `import pulse.bot`（代码侧；本设计文档对照表除外）
- [x] `DingTalkChannelHandler`、`ChannelMessenger`、`handle_channel_command` 就位
- [x] `pulse channel` 为正式 CLI；`pulse serve` 仍可用并有 deprecation 提示
- [x] 现行文档将进程表述为 Channel Adapter / channel，而非 bot 大脑
- [x] 相关测试通过（`test_dev_manager` + channels/handler/cutover/mirror 等；全量中 `test_capability_registry_seed` 失败为既有 seed 计数问题，与本次改名无关）

## 决策记录

- 范围：C（运行时 + 包 + CLI `channel`，`serve` 别名）
- 边界：对话选项 C（文档尽量改），但实施上**排除第三方平台术语**；历史**文件名**不改
- 落地：方案 2（分层 + 硬排除），无 `pulse.bot` shim
