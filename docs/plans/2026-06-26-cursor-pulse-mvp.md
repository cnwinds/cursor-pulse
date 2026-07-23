# Cursor Pulse MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建钉钉机器人驱动的 Cursor 用量收集系统 MVP：CSV 规则解析 → SQLite 存储 → 代码聚合 → 双通道提交与定时催办。

**Architecture:** Python 单体服务；`dingtalk-stream` 长连接收消息；CSV 主路径零 LLM；APScheduler 跑每日催办/截止提醒；聚合与报告数字纯 pandas/SQL。

**Tech Stack:** Python 3.11+, SQLAlchemy 2, pandas, dingtalk-stream, APScheduler, pydantic-settings, pytest

---

## Phase 1 文件地图

| 路径 | 职责 |
|------|------|
| `pulse/config.py` | 加载 config.yaml + 环境变量 |
| `pulse/storage/models.py` | ORM 表定义 |
| `pulse/storage/db.py` | 引擎与会话 |
| `pulse/storage/repository.py` | 提交/成员/缺报查询 |
| `pulse/extract/csv_parser.py` | usage-events.csv 确定性解析 |
| `pulse/extract/summary.py` | 提交确认摘要生成 |
| `pulse/aggregate/engine.py` | 账期聚合 → metric_snapshots |
| `pulse/channels/dingtalk/handler.py` | Stream 消息路由 |
| `pulse/channels/reminders/scheduler.py` | 收集期/每日/截止任务 |
| `pulse/channels/commands.py` | /status /report 等 |
| `pulse/cli.py` | CLI 入口 |
| `pulse/app.py` | 启动 bot + scheduler |
| `tests/test_csv_parser.py` | 样本 CSV 498 行基准测试 |
| `tests/test_aggregator.py` | 聚合可复现测试 |

---

### Task 1: CSV 解析器 ✅

- [x] 解析 12 列 usage-events 格式
- [x] Cost 四类映射（Included/Free/-/数字）
- [x] `source_row_hash` 去重键
- [x] 样本 498 行全通过

### Task 2: 数据库与仓储 ✅

- [x] members / submissions / usage_records / metric_snapshots / reminder_logs
- [x] 账期覆盖写入（最新 submission 替换旧记录）
- [x] OQ-9：首次提交 pending，管理员添加才 active

### Task 3: 聚合引擎 ✅

- [x] 输出 PRD 6.3 事实层指标 JSON
- [x] `computation_version` 固定字符串保证可复现

### Task 4: 钉钉 Bot 网关 ✅

- [x] 单聊 file 消息 → 下载 CSV → 解析入库
- [x] 群聊 @机器人 → 群内极简确认 + OTO 私聊摘要
- [x] `DingTalkMessenger`：download / oToMessages / groupMessages

### Task 5: 提醒调度 ✅

- [x] 接入真实 `send_group_message` / `send_private_message`

### Task 6: CLI 与启动 ✅

- [x] `pulse parse/import/aggregate/export/report/serve`
- [x] `pulse remind start|daily|deadline|report`

### Task 7: 报告与查询 ✅

- [x] 月报模板 + 规则洞察（数字来自 metrics）
- [x] 自然语言查询（规则 → pandas）
- [x] Bot 命令：报告、查询、成员、帮助、导出
- [x] 每月 4 日 11:00 自动发月报

---

## Phase 1 Definition of Done

1. `pytest` 全绿，样本 CSV 498 行解析正确
2. `pulse import` + `pulse aggregate` 产出可对账 snapshot
3. `pulse serve` 连接钉钉（需凭证）完成私聊/群聊提交闭环
4. 调度任务可手动 `pulse remind daily|deadline|start|report` 触发
5. `docker compose up` 可生产部署

---

## Phase 2 — 完善 ✅

- [x] Docker Compose 单机部署
- [x] 文本粘贴 CSV 提交
- [x] 截图接收 + 降级提示（Vision 待接入）
- [x] LLM 报告叙述层（数字仍来自 snapshot，含审计 + 规则降级）
- [x] Vision 截图结构化提取（置信度阈值 + 低置信度拒绝入库）
- [x] Web 管理后台（成员 / 提交进度 / 指标 / 查询审计）

---

## Phase 3 — 增强 ✅

- [x] 多团队租户（Team + team_id 隔离，PULSE_TEAM_SLUG）
- [x] FinOps/BI Webhook（月报后自动推送 + `pulse bi-push`）
- [x] 异常检测告警（规则引擎 + 管理员私聊 + `pulse alerts`）

---

## Phase 4 — 后续完善 ✅

- [x] Postgres 部署（`docker-compose.postgres.yml` + `DATABASE_URL`）
- [x] S3 对象存储归档（`object_storage` + `pip install -e '.[s3]'`）
- [x] PDF 月报（`pulse report --pdf out.pdf`）
- [x] 低置信度截图人工确认（`pending_review` + 待审/确认/拒绝）
- [x] Bot 平台抽象 + 飞书/企微扩展桩（`docs/platforms/`）
- [x] Cursor Teams Admin API 桩（`pulse teams-api`）
