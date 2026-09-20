# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **Auto Lender 自动选号**：Key Loan 支持 `lender_mode=auto`，不再在发放时锁死出借账号。由「硬过滤 → 算法分 → Jev 决策 → 护栏」选出账号，并每隔至少 30 分钟重评一次。自助借 Key 默认走自动模式。
- **Jev 主判（OpenRouter Decisions）**：接入 TypeSafe System One 决策模型 `typesafe/jev-1.13`，用 `choice` 问该借哪个账号、`noul` 逐候选问是否侵占主负责人预留。它只在存活候选上重排，调用失败 / 置信度不足 / 首选与次优间隔过小 / 判定影响主负责人时一律回落算法分；带特征哈希缓存与连续失败熔断。可在「系统设置 → Jev 决策模型」配置，或走 `JEV_*` 环境变量。
- **按 Quota Pool 打分**：给出目标模型时按该模型所属桶（`auto` / `api`）取余量与空闲额度，`unknown` 退化为两桶都要有余量；新增 `pulse/tool_center/quota_pool.py` 镜像 Go `quotaPoolForModel`。
- **主负责人保留量**：账号级 `proxy_reserve_pct`（默认取 `loan_selection.owner_reserve_pct`）。号主按当前速率外推到作废日会吃掉保留量时，该账号作为出借方被硬排除（`owner_reserve`）。
- **Switch dwell 最小驻留**：`loan_selection.min_switch_minutes`（默认 30 分钟）内只降权不排除；Go 代理新增 `PROXY_STICKY_MIN_DWELL`（默认 30m，`0`/`off` 关闭），保证请求时也不因桶耗尽频繁换账号。
- **借用自动选号预览**：`POST /api/v2/loans/auto-pick` 返回打分排序 + Jev 决策；「为成员分配 Key」弹窗选自动模式时先看预计选中账号再确认。

### 变更

- **人工分统一两条路径**：`proxy_score_adjust` 此前只影响 Credential Pool 顺序、不影响借 Key 出借排序（CONTEXT.md 明确写过），现在两条路径共用同一字段与语义。
- **借用列表**：新增 `lender_mode` / `source_bound_at` 字段；打分表新增「主负责人保留」列与 Jev 决策提示。
- **配置**：新增 `LoanSelectionConfig` 的 `min_switch_minutes` / `recency_penalty` / `owner_reserve_pct` / `auto_mode` / `auto_top_n` / `auto_min_confidence` / `auto_min_margin` / `auto_cache_seconds` / `auto_switch_margin`，以及 `JevConfig`。
- **迁移**：`key_loans` 增 `lender_mode`（默认 `manual`）与 `source_bound_at`（存量记录回填为 `created_at`），`ai_accounts` 增 `proxy_reserve_pct`。

### 修复

- **打分表两个微调互相清空**：`/proxy-pool/accounts/{id}/score` 把「未传 `score_adjust`」当成显式清空，保存「主负责人保留」会静默抹掉已有的人工分。改为按 `model_fields_set` 只更新显式传入的字段。
- **auto 模式选号误报「账号不存在」**：`loan-key` 在解析 `lender_mode` 之前先用 URL 上的 `account_id` 校验账号，前端未预选账号时发出的占位 id 会直接 404。auto 模式跳过该校验。
- **换绑/发放失败残留远端 Key**：远端 Key 已创建但本地事务失败时，只回滚数据库，Cursor 侧那条 Key 因无本地记录而无法回收。改为失败即 best-effort 吊销。
- **日趋势图例重叠**：ECharts 6 默认把 legend 放在底部，grid 底部留白不够，图例会叠在日期和矮柱上。概览 / 用量分析共用的日趋势改为顶部图例，并加大 `grid.top`。
- **概览与用量分析日趋势对齐**：两页共用同一套日聚合；日趋势改为堆叠柱（输入/输出/cache，柱高=当日总 Token）+ 花费折线，避免平滑面积图把相邻日「鼓包」。概览改为本账期全日序列，不再截成近 14 天。

### 变更（概览与用量）

- **概览日趋势改为近 30 天**：首页图表窗口与用量分析「近 30 天」对齐（含上月后半段）；本账期花费 / Tokens KPI 仍按当前账期汇总。
- **额度看板按日明细**：日期改为由近到远（逆序）展示
- **读路径性能**：额度看板 / 出借推荐 / 总览不再拉全量历史配额快照；同步后每账号只保留最近 48 条快照。总览改用轻量同步计数与 SQL 聚合用量 KPI，集成区块不再加载 chat_memory。看板可一次带上本周期 UsageSummary，避免再打多月 `/usage-summaries`。用量分析 overview 改为 SQL 分组，不再把日聚合整表载入内存。借用列表与 Proxy Key 列表改为批量汇总用量。

### 文档

- 新增 `docs/adr/0001-auto-lender-selection.md`，记录「Jev 主判 + 算法分保底 + 硬过滤权威」的取舍。
- `CONTEXT.md` 增补 Manual Rank Score（改写）、Pool-scoped Headroom、Owner Reserve、Switch Dwell、Auto Lender Selection、Jev Decision 词条。

## [0.4.0] - 2026-08-25

### 新增

- **共享池人工分**：打分表可为账号指定微调分，加在算法综合分上调整入池排名（硬过滤仍生效；不影响借 Key 出借排序）
- **按 Cursor kind 拆分 included / BYOK 用量**：日聚合与分析区分套餐内与 `USER_API_KEY`；额度看板 API 进度跟快照 `api_pct`（不含第三方）

### 变更

- **代理窗口限额延后到请求时**：`window_limited` 不再在 exchange/登录失败（避免 CLI 误报 invalid API key）；登录仍可成功，业务请求返回可展示的 `resource_exhausted` 限额说明
- **共享池打分余量**：按 Snapshot Headroom（`total_pct`）估余量，避免 `planUsage.remaining` 低估空闲号导致排序偏差
- **作废时刻用 Cursor `billingCycleEnd` 精确时钟**：同步写入 `cycle_end_at`；`hours_to_deadline` 不再按 UTC 日终虚报；打分表「作废时刻」按中国时间展示

### 修复

- gzip Connect 帧内 `TurnEnded` token 计数与额度看板 spend 对齐；拒绝错计费周期摘要污染看板
- 从 gzip Connect `providerOptions` 提取 billed model

### 杂项

- 记录 GitHub Release 英文正文约定（`docs/agents/release.md`），并从 `AGENTS.md` 链接

## [0.3.0] - 2026-08-06

### 新增

- **出站消息入会话账本**：Key 借用通知、知识库群推、渠道本地回复等 Pulse 出站 IM，在投递成功后写入 Assistant 会话账本（挂当前 open 会话，无则新建）；`pk_` / `pka_` 入账脱敏
- **门户 access/refresh 鉴权**：安全轮换与静默续期
- **Proxy 模型感知配额池路由**：按模型选择配额池，并支持 stream body wait

### 变更

- **借 Key / Proxy 模块加深**：拆分 Loan Lifecycle、CredentialQuotaState，以及 authorize / usage ledger / pool board 等控制面缝
- **代理池打分与用量明细**：改进 pool scoring；借 Key 用量按出借账号展示，明细列整理
- **默认 `PROXY_EXHAUSTED_RESET=30m`**：周期性清理 sticky 配额标记

### 修复

- 额度看板用量摘要按计费周期月份加载
- 借 Key 代理用量按借用区间展示
- Proxy session sticky rotation 与 Run 请求模型提取

### 杂项

- 路线图后续条目改为「待定」；README 演示视频与视觉一致性更新
- 借 Key 相关测试快照计费周期改为相对今天，避免周期结束后 CI 误伤

## [0.2.0] - 2026-07-30

### 新增

- **管理后台概览重设计**：聚合「需要关注」待办、核心 KPI、近 14 天用量趋势、额度风险 Top 5 与最近动态；按登录用户权限裁剪
- **MITM Proxy 限额流检测**：识别 gzip 压缩的 Connect end-stream 与 `InteractionUpdate.post_request_prompt` 限额信号；新增 `PROXY_DEBUG_STREAM` 响应帧诊断
- **品牌与文档**：英文为主 README（`README_CN.md` 保留中文版）、内嵌演示视频、SVG Logo、社交预览图、`NOTICE` 免责声明

### 变更

- **Proxy 限额轮换策略**：限额/配额错误原样返回 CLI，代理侧标记账号 exhausted 并 advance pool；用户重发消息即走下一账号（移除同请求内透明重试，避免 CLI reconnecting/无输出）
- **Proxy 会话与模型路由**：修复 session sticky rotation 与 Run 请求模型提取；支持按模型感知的配额池路由与 stream body wait
- **路线图**：后续条目改为「待定」，不构成排期承诺

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

[Unreleased]: https://github.com/cnwinds/cursor-pulse/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cnwinds/cursor-pulse/releases/tag/v0.1.0
