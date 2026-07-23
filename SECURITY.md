# 安全策略

## 支持的版本

安全修复以公开仓库的默认分支（`master` / `main`）为准。正式发版 tag 出现后会在此补充列表。

## 威胁模型（摘要）

Cursor Pulse 是**自托管**控制面。敏感资产包括：

- Cursor / 厂商 API Key 与代理池凭证
- 钉钉应用 Secret
- JWT / 内部 service token
- 可选：Go 数据面代理的 MITM CA 私钥

信任边界：Web 管理后台、Internal HTTP API、钉钉渠道、Assistant 服务、可选 Go MITM 代理。

**非目标：** 客户端对本地 MITM CA 使用 `-k` / 跳过 TLS 校验时的传输完整性。

## 报告漏洞

请**不要**在公开 issue 中附带密钥或可直接利用的细节。

优先使用 GitHub 公开仓的 [Security Advisories](https://github.com/cnwinds/cursor-pulse/security/advisories/new)，或联系 `pyproject.toml` / GitHub 主页上的维护者。

请说明：受影响版本或 commit、影响面、复现步骤，以及是否计划协调披露。

## 密钥处理

- 将 `.env.example` 复制为 `.env`，将 `config.example.yaml` 复制为 `config.yaml`。**切勿提交** `.env`、`config.yaml`、`*.db`、`*.pem`、`*.key` 或 `.dev/` 调试落盘。
- 为以下项生成高熵随机值：`JWT_SECRET`、`PULSE_CREDENTIAL_ENCRYPTION_KEY`、`PULSE_INTERNAL_SERVICE_TOKEN`、`ASSISTANT_SERVICE_TOKEN`、`ASSISTANT_SECRET_KEY`。
- 生产环境禁止使用 `change-me-*` 等占位令牌（见 `docker/scripts/setup.sh`；应用启动也会拒绝）。
- 一旦疑似泄露请立即轮换；更改 `PULSE_CREDENTIAL_ENCRYPTION_KEY` 后需按 `docs/RUNBOOK.md` 重加密已存凭证。

## 鉴权说明

- 内部路由（`/api/internal/v1/*`）必须配置 service token；未配置时应失败关闭（fail closed）。
- 设置项「揭密 / reveal」会返回明文密钥——请谨慎授予管理员角色。
- `ADMIN_PASSWORD` / `ADMIN_WEB_TOKEN` 属于灾备路径；日常优先使用门户 bootstrap 账号。

## 部署加固

- 勿在无 TLS 终结与网络隔离的情况下，将 web / proxy 监听直接暴露到公网。
- MITM CA 私钥仅保存在可信运维机器；泄露后立即轮换。
- `PROXY_DEBUG_USAGE` 会落盘请求体——仅限本地调试，切勿提交调试目录。
