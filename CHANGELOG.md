# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.1.0] - 2026-07-28

首个公开发布版本。cursor-pulse 以 MIT 协议开源：自托管的 Cursor 团队用量计量与额度控制面。

### 新增

- **账号台账**：登记团队 Cursor 账号，指定负责人，绑定 Cursor User API Key
- **用量自动同步**：绑 Key 后按周期自动拉取 Cursor 用量并入账，为唯一用量来源
- **额度看板**：与 Cursor Plan & Usage 对齐的看板，按计费周期展示额度消耗与成员分布
- **用量分析**：团队/成员维度的用量统计视图
- **借 Key**：成员自助申请临时 Key，到期自动回收；支持管理员代分配与转借
- **On-Demand 支出管控**：支出限额设置与超限通知
- **管理后台**：Vue 门户，Web-only 为默认开源路径；门户登录与 IM 解耦（MemberIdentity）
- **可选 IM 插件**：钉钉 / 飞书机器人（渠道化架构，不承载用量采集）
- **可选 MITM Proxy**：Go HTTPS 代理，透明轮换与用量归因；代理 Key 按 5h/7d 美元成本窗口限流
- **Docker 一键部署**：`docker compose up -d` 一条命令起全栈（自动 init-db）
- 团队展示时区统一设置；中文文档与中文 README

### 安全

- 生产环境强制要求 `JWT_SECRET`
- `ADMIN_PASSWORD` 支持哈希存储
- 凭据加密存储（`PULSE_CREDENTIAL_ENCRYPTION_KEY`）
- Pulse 与 Assistant 之间的 actor 声明使用 HMAC 签名；拒绝不安全的服务 token 启动

### 已知限制

- 用量同步依赖 Cursor 未公开 API，可能随 Cursor 升级失效（见 [docs/cursor-usage-api.md](docs/cursor-usage-api.md)）
- MITM Proxy 需终端信任自签 CA，存在合规风险，默认不启用（见 [proxy/README.md](proxy/README.md)）

[Unreleased]: https://github.com/cnwinds/cursor-pulse/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cnwinds/cursor-pulse/releases/tag/v0.1.0
