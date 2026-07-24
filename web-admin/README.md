# Cursor Pulse Web Admin

Vue 3 + Element Plus 管理后台，对接 `pulse web` API。

## 开发

```bash
# 终端 1：后端 API
pip install -e ".[web]"
pulse web

# 终端 2：前端
cd web-admin
npm install
npm run dev
```

浏览器打开 http://localhost:5173

## 生产部署（同域）

```bash
cd web-admin
npm ci && npm run build   # 输出到 pulse/web/static/
pip install -e ".[web]"   # package-data 带上 static/
pulse web                 # 托管 http://host:8080/admin/
```

构建产物 base 为 `/admin/`，唯一路径为 `pulse/web/static/`（与 Docker / `pip install` 一致）。
开发覆盖可用环境变量 `PULSE_ADMIN_STATIC_DIR`。

开发模式仍为 `http://localhost:5173`。

## 首次登录

```bash
pulse admin bootstrap --user-id <钉钉userid> --name "管理员" --password <临时密码>
```

或使用 `config.yaml` / `DINGTALK_ADMIN_USER_IDS` 中的 userid 自动迁移为 `owner`，配合 `ADMIN_WEB_TOKEN` 访问旧版 HTML 面板。

## 钉钉 OAuth

在钉钉开放平台配置重定向 URL：`http://localhost:5173/login/callback`（生产环境改为实际域名）。

环境变量：

- `JWT_SECRET` — JWT 签名密钥（推荐）
- `DINGTALK_OAUTH_REDIRECT_URI` — OAuth 回调地址
- `WEB_CORS_ORIGINS` — 逗号分隔的 CORS 来源
