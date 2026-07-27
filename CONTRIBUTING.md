# 贡献指南

感谢参与 Cursor Pulse 的贡献。

## 前置依赖

- Python ≥ 3.11
- Node.js 20+（仅当修改 `web-admin/` 时需要）
- Go 1.22+（仅当修改 `proxy/` 时需要）

## 本地搭建

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev,web]"            # [dev] 已含 dingtalk/feishu 测试依赖
copy config.example.yaml config.yaml   # Unix 用 cp
copy .env.example .env                 # 最少：ADMIN_PASSWORD / JWT_SECRET / 加密密钥
pulse init-db                          # 建表 + seed 厂家/套餐
```

最小 UI/API 联调：

```bash
# Windows
.\cursor-pulse.bat start web admin
# macOS/Linux
./cursor-pulse.sh start web admin
```

完整本地栈（channel + assistant + 可选 proxy）见根目录 `README.md` 与 `docs/RUNBOOK.md`。

## 测试（PR 门禁）

```bash
pytest --tb=short -q
```

若改动了 Go 代码：

```bash
cd proxy && go test ./...
```

若改动了管理后台：

```bash
cd web-admin && npm ci && npm run build   # writes pulse/web/static/
```

## Docker

请始终在 `docker/` 下操作（见根 `README.md` 与 `docs/RUNBOOK.md`）。**不要**再使用仓库根目录的 `docker compose`。

```bash
cd docker
./scripts/setup.sh    # Windows Git Bash 可用 bash scripts/setup.sh
docker compose build
docker compose up -d                              # Web-only：init-db + web + channel
docker compose --profile full up -d               # 含 Assistant
```

排查 schema 迁移失败时可手动：`docker compose run --rm init-db`。

## 提交与评审

- 优先使用 [Conventional Commits](https://www.conventionalcommits.org/)：`feat` / `fix` / `chore` / `docs` / `refactor` / `test`。
- PR 尽量聚焦单一目的；有 issue 时请关联。
- 勿提交密钥、数据库、CA 私钥，以及个人本机路径 / 邮箱。

## 安全

见 [SECURITY.md](SECURITY.md)。切勿在 issue / PR 中粘贴生产环境令牌。
