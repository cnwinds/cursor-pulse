# Channel Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 bot 进程/包/CLI/文档命名对齐为 channel（Channel Adapter），不改变业务行为。

**Architecture:** 先改开发服务与 CLI 表层，再 `git mv pulse/bot → pulse/channels` 并替换符号与 import，最后扫文档与跑测试。不保留 `pulse.bot` shim；`pulse serve` 保留为 deprecated 别名。

**Tech Stack:** Python、pytest、现有 `pulse.dev` / `pulse.cli`、Markdown 文档

**Spec:** `docs/superpowers/specs/2026-07-15-channel-rename-design.md`

---

### Task 1: 开发服务 bot → channel

**Files:**
- Modify: `pulse/dev/services.py`
- Modify: `pulse/dev/manager.py`（所有 `service == "bot"` / `name == "bot"`）
- Modify: `pulse/cli.py`（`_dev_services`、help 文案）
- Modify: `cursor-pulse.ps1`、`cursor-pulse.sh`、`cursor-pulse.bat`
- Modify: `tests/test_dev_manager.py`

- [ ] **Step 1: 更新失败测试期望**

在 `tests/test_dev_manager.py` 中：

```python
def test_default_services():
    assert DEFAULT_SERVICES == ("web", "admin", "channel", "assistant")
    assert set(SERVICES) == {"web", "admin", "channel", "assistant"}


def test_build_command_channel_includes_reload():
    from pulse.dev.services import build_command

    command, _cwd, extra = build_command("channel", config_path="config.yaml")
    assert "--reload" in command
    assert "channel" in command  # 正式 CLI；过渡期若仍调 serve 则 assert serve 且另测 channel
    assert extra == {}
```

（实现 Task 2 后，`build_command` 应调用 `pulse channel --reload`。）

- [ ] **Step 2: 改 services / manager / cli / 启动脚本**

`SERVICES` / `DEFAULT_SERVICES` 键改为 `channel`，label 改为「渠道适配 + 调度」。

`build_command("channel")` 暂时仍可执行 `[*pulse, "-c", config_path, "serve", "--reload"]`，直到 Task 2 改为 `channel`。

`manager.py` 中 bot 特判改为 `channel`（`_find_bot_serve_pids` 可改名为 `_find_channel_serve_pids`）。

CLI `_dev_services = ["web", "admin", "channel", "assistant"]`。

启动脚本中服务列表与帮助文案同步。

- [ ] **Step 3: 跑测试**

```bash
pytest tests/test_dev_manager.py -v
```

Expected: PASS（若 Step 1 已要求 command 含 `channel` 而 build 仍用 serve，则先让 assert 仍检查 `serve`，Task 2 再改 assert）

- [ ] **Step 4: Commit（仅当用户要求时）**

---

### Task 2: CLI `pulse channel` + `serve` 弃用别名

**Files:**
- Modify: `pulse/cli.py`
- Modify: `pulse/dev/services.py`（`build_command` 改为调用 `channel`）
- Modify: `pulse/app.py`（若有 bot 日志文案）
- Modify: `tests/test_dev_manager.py`

- [ ] **Step 1: 增加 `channel` 子命令**

```python
p_channel = sub.add_parser("channel", help="Start channel adapter + reminder scheduler")
p_channel.add_argument("--reload", action="store_true", help="开发模式：代码变更时自动重启")

p_serve = sub.add_parser("serve", help="(deprecated) 请改用: pulse channel")
p_serve.add_argument("--reload", action="store_true", help="开发模式：代码变更时自动重启")
```

处理分支：

```python
if args.command in ("channel", "serve"):
    if args.command == "serve":
        logger.warning("「pulse serve」已弃用，请改用「pulse channel」")
    # 原 serve 启动逻辑不变
```

- [ ] **Step 2: build_command 改用 channel**

```python
if service == "channel":
    return (
        [*pulse, "-c", config_path, "channel", "--reload"],
        root,
        {},
    )
```

- [ ] **Step 3: 更新 test_build_command_channel_includes_reload 断言含 `"channel"`**

- [ ] **Step 4: `pytest tests/test_dev_manager.py -v` → PASS**

---

### Task 3: 包迁移 pulse/bot → pulse/channels

**Files:**
- Move: `pulse/bot/` → `pulse/channels/`
- Modify: 全仓 `from pulse.channels` / `import pulse.channels` / `pulse.channels.` 字符串
- Modify: `docs/platforms/*.md` 中的模块路径

- [ ] **Step 1: 移动目录**

```bash
git mv pulse/bot pulse/channels
```

Windows 若大小写/占用失败，用两步 mv 或复制后删。

- [ ] **Step 2: 替换 import**

全仓将 `pulse.channels` 替换为 `pulse.channels`（含测试 patch 路径如 `pulse.channels.dingtalk.mirror`）。

- [ ] **Step 3: 冒烟**

```bash
python -c "import pulse.channels.base; import pulse.channels.dingtalk.handler"
pytest tests/test_dingtalk_mirror.py tests/test_command_cutover.py -v --tb=no -q
```

Expected: import 成功；测试可能因旧类名失败，Task 4 修复。

---

### Task 4: 符号重命名

**Files:**
- Modify: `pulse/channels/base.py` — `BotMessenger` → `ChannelMessenger`
- Modify: `pulse/channels/dingtalk/handler.py` — `PulseBotHandler` → `DingTalkChannelHandler`（仍继承 `dingtalk_stream.ChatbotHandler`）
- Modify: `pulse/channels/dingtalk/client.py` 及所有引用
- Rename: `pulse/capabilities/handlers/bot_command.py` → `channel_command.py`
- Modify: `handle_bot_command` → `handle_channel_command`；`pulse/capabilities/invoke.py` 等

- [ ] **Step 1: 改 Protocol / Handler 类名并更新引用**

- [ ] **Step 2: 改能力 handler 文件与函数名**

```bash
git mv pulse/capabilities/handlers/bot_command.py pulse/capabilities/handlers/channel_command.py
```

- [ ] **Step 3: 跑相关测试**

```bash
pytest tests/test_command_cutover.py tests/test_capability_bridge.py tests/test_dingtalk_mirror.py tests/test_takeover.py tests/test_dev_manager.py -v --tb=short
```

Expected: PASS

---

### Task 5: 文档扫尾

**Files:**
- Modify: `README.md`、`docs/RUNBOOK.md`、`docs/platforms/feishu.md`、`docs/platforms/wecom.md`
- Modify: `docs/superpowers/plans/**`、`docs/superpowers/specs/**` 正文中对我们架构的 bot 叙述
- 不改：历史计划**文件名**；钉钉 `robot_code` / SDK 术语

- [ ] **Step 1: 将「Pulse Bot / bot 进程 / 钉钉机器人 + 调度」改为 Channel Adapter / channel 进程等**

- [ ] **Step 2: 保留钉钉 robot 配置句（如 `DINGTALK_ROBOT_CODE`）**

- [ ] **Step 3: 确认无残留 `from pulse.channels`**

```bash
rg "from pulse\.bot|import pulse\.bot|pulse/bot" --glob '!*.pyc' --glob '!.worktrees/**'
```

Expected: 无匹配（或仅硬排除项）

---

### Task 6: 全量验收

- [ ] **Step 1: 跑 pytest 核心集**

```bash
pytest tests/test_dev_manager.py tests/test_command_cutover.py tests/test_capability_bridge.py tests/test_dingtalk_mirror.py tests/test_takeover.py tests/test_dingtalk_messenger.py tests/test_dingtalk_guide_image.py -v --tb=short
```

- [ ] **Step 2: 理想全量**

```bash
pytest -q --tb=line
```

- [ ] **Step 3: 对照 spec 验收清单勾选**

---

## Spec 覆盖自检

| Spec 项 | Task |
|---|---|
| 服务名 channel | 1 |
| CLI channel + serve 别名 | 2 |
| 包 pulse/channels | 3 |
| ChannelMessenger / DingTalkChannelHandler / handle_channel_command | 4 |
| 文档 | 5 |
| 硬排除 SDK/robot | 全程 |
| 无 pulse.bot shim | 3 |
| 验收测试 | 6 |
