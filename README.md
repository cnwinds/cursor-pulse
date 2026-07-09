# Cursor Pulse

钉钉机器人 + Web 台账管理 AI 工具用量：Cursor 通过 API Key 自动同步，其他工具支持手工/截图提交，确定性聚合，隐私优先。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ".[dev]"

copy config.example.yaml config.yaml
# 编辑 config.yaml 或设置环境变量：
# DINGTALK_APP_KEY, DINGTALK_APP_SECRET, DINGTALK_GROUP_ID

pulse init-db
pulse parse samples/usage-events-sample.csv
pulse import samples/usage-events-sample.csv --user-id u1 --name Alice --period 2026-06
pulse aggregate --period 2026-06
pytest
```

## 钉钉机器人

1. 在 [钉钉开放平台](https://open-dev.dingtalk.com/) 创建企业内部应用
2. 启用机器人，选择 **Stream 模式**
3. 开通权限：接收群/单聊消息、企业内机器人发送消息、媒体文件下载
4. 配置 `robot_code`（机器人 ID，与 AppKey 不同，在机器人详情页查看）
5. 配置 `group_open_conversation_id`（目标群的 openConversationId）
6. 将机器人拉入 AI 使用交流群
7. 配置 `config.yaml` 后运行：

```bash
pulse serve
```

### 消息通道说明

| 场景 | 实现 |
|------|------|
| 私聊绑定 Cursor API Key | Stream 收消息 → 加密存储 Key → 每日自动同步 |
| 私聊提交其他工具用量 | Stream 收 file/截图/文本 → 私聊回复摘要 |
| 群内 @机器人 提交 | 群内极简确认 + **OTO API** 私聊完整摘要 |
| 定时群广播 / 截止提醒 | `groupMessages/send` API |
| 每日缺报催办 | `oToMessages/batchSend` API |

> 企业内部机器人主动群消息接口暂不支持原生 @所有人，截止提醒会在正文前加 `【@所有人】` 标记；如需真正 @all 触达，需机器人具备群主权限或使用 session 回复场景。

## Docker 部署

```bash
# 确保 config.yaml 与 .env 已配置
docker compose build
docker compose run --rm pulse pulse init-db -c /app/config/config.yaml
docker compose up -d
docker compose logs -f pulse
```

管理后台（Vue 3 + JWT，见 [Runbook §4.5](docs/RUNBOOK.md)）：

```bash
pip install -e ".[web]"
pulse admin bootstrap --user-id <钉钉userid> --name "管理员" --password <密码>
pulse web --port 8080

# 开发 UI
cd web-admin && npm install && npm run dev   # http://localhost:5173

# 生产：npm run build 后访问 http://host:8080/admin/
```

仍支持 `ADMIN_WEB_TOKEN` 机器访问与根路径简易 HTML 面板。

数据持久化在 `./data` 目录（SQLite + 原始 CSV/截图）。更多运维细节见 [Runbook](docs/RUNBOOK.md)。

### 进阶能力速查

```bash
# 多团队 / BI / 告警
pulse bi-push --period 2026-06
pulse alerts --period 2026-06

# Postgres
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d

# PDF 月报
pip install -e ".[pdf]"
pulse report --period 2026-06 --pdf reports/2026-06.pdf
```

群里管理员：`告警 2026-06` · `待审` · `确认 <id前8位>` · `拒绝 <id前8位>`

### LLM 报告洞察（可选）

默认使用规则洞察；启用 LLM 后，洞察段由模型生成，且会审计数字是否均来自 snapshot：

```yaml
# config.yaml
llm:
  enabled: true
  model: "gpt-4o-mini"
```

```bash
# .env
LLM_ENABLED=true
LLM_API_KEY=sk-...
# LLM_BASE_URL=https://your-proxy/v1   # 可选

# 截图 Vision 提取（需支持 vision 的模型，如 gpt-4o）
VISION_ENABLED=true
VISION_MODEL=gpt-4o
```

## CLI 命令

| 命令 | 说明 |
|------|------|
| `pulse parse <csv>` | 解析 CSV 并输出摘要 |
| `pulse import <csv> --user-id --period` | 导入数据库 |
| `pulse aggregate --period` | 运行聚合 |
| `pulse report --period YYYY-MM` | 生成月报（加 `--publish` 发群） |
| `pulse export --period YYYY-MM` | 导出明细 CSV |
| `pulse remind report` | 手动发布月报 |
| `pulse serve` | 启动 Stream 机器人 + 调度器 |
| `pulse web` | 管理后台 API（+ 可选 Vue `/admin/`） |
| `pulse admin bootstrap` | 创建 Web 门户 owner |
| `pulse memory evolve` | 小脉记忆自进化 |

## 文档

- [PRD](docs/PRD.md)
- [运维 Runbook](docs/RUNBOOK.md)
- [实现计划](docs/plans/2026-06-26-cursor-pulse-mvp.md)
