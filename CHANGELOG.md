# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [0.2.0] - 2026-07-29

### 新增

- **管理后台概览重设计**：聚合「需要关注」待办、核心 KPI、近 14 天用量趋势、额度风险 Top 5 与最近动态；按登录用户权限裁剪
- **MITM Proxy 限额流检测**：识别 gzip 压缩的 Connect end-stream 与 `InteractionUpdate.post_request_prompt` 限额信号；新增 `PROXY_DEBUG_STREAM` 响应帧诊断
- **品牌与文档**：英文为主 README（`README_CN.md` 保留中文版）、内嵌演示视频、SVG Logo、社交预览图、`NOTICE` 免责声明

### 变更

- **Proxy 限额轮换策略**：限额/配额错误原样返回 CLI，代理侧标记账号 exhausted 并 advance pool；用户重发消息即走下一账号（移除同请求内透明重试，避免 CLI reconnecting/无输出）

### 修复

- Proxy `go test` CI：model-tap 测试内联 fixture，session/TTL 测试与 quota 轮换解耦
- 管理后台深色模式下 Logo 可读性（`prefers-color-scheme`）

### 杂项

- 停止跟踪内部 `docs/superpowers/`；移除冗余 GitLab CI

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

[Unreleased]: https://github.com/cnwinds/cursor-pulse/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cnwinds/cursor-pulse/releases/tag/v0.1.0
