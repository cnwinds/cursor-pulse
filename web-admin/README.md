# Cursor Pulse Web Admin

Vue 3 + Element Plus 管理后台，对接 `pulse web` API。

## 开发

```bash
# 终端 1：后端 API
pip install -e ".[web]"
pulse init-db   # 首次：建表 + seed 厂家/套餐
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

## 首次登录（Web 默认）

1. 在 `.env` 设置 `ADMIN_PASSWORD` 与 `JWT_SECRET`（生产 ≥32 字节）
2. 浏览器打开管理后台，用用户名 **`admin`** + `ADMIN_PASSWORD` 登录  
   （首次成功后会写入库内 `password_hash`）
3. 或 CLI：

```bash
pulse admin bootstrap --user-id admin --name "管理员" --password '<密码>' --channel web
```

普通成员可在「我的借用」自助申请临时 Key；管理员在「借用记录」代分配。

## 可选：钉钉 / 飞书扫码登录

配置对应应用凭证后，`/api/auth/providers` 才会暴露扫码入口。重定向 URL 示例：`http://localhost:5173/login/callback`（生产改为实际域名）。

环境变量：

- `JWT_SECRET` — JWT 签名密钥（推荐）
- `DINGTALK_OAUTH_REDIRECT_URI` / 飞书等价配置 — OAuth 回调地址
- `WEB_CORS_ORIGINS` — 逗号分隔的 CORS 来源
