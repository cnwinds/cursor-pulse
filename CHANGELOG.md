# Changelog

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 约定，版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### 新增

- **后台左上角显示版本号**：构建时从 `pyproject.toml` 读版本号。HEAD 恰好在干净的 `v<版本>` tag 上算正式版，只显示 `v0.7.0`；其余情况是开发版，显示 `DEV v0.7.0 · <commit>`，有未提交改动时 commit 后带 `*`。悬停可看完整信息，点击复制。Docker 构建通过 `PULSE_BUILD_DESCRIBE` / `PULSE_BUILD_COMMIT` 构建参数传入 git 信息（见 RUNBOOK）。

### 变更

- **复制命令改为弹窗**：借用管理与「我的借用」的「复制命令」从下拉菜单改为弹窗，分 CLI / IDE 两个标签，命令可预览（密钥中段打码）后逐条复制；多个代理地址时在弹窗顶部切换。IDE 标签提供**安装命令**与**恢复命令**（`irm <代理>/uninstall-cursor.ps1 | iex`，移除 Cursor 代理设置与 CA，不含密钥）；`client-setup?kind=ide` 响应新增 `uninstall_command`。
- **IDE 安装脚本不再自动启动 Cursor**：`setup-cursor.ps1` 完成后提示用户自行启动 Cursor（已打开的需完全退出再重开）即可使用。

### 移除

- **v0.7.0 过渡期结束**：从 v0.7.0 之前的版本升级，须先升到 v0.7.0 并完成全部 Go 代理升级、成员重跑 IDE setup，再升级到本版。
- **内部 `/api/internal/v1/proxy/loan-usage-cap`**：已删除（返回 404），Go 代理统一调用 `/spend-check`；`pulse/proxy/loan_usage_cap.py` 兼容层一并删除。
- **每 key 专属 IDE 端口**：删除 `/ide-port`（GET/DELETE，现返回 404）、专属监听与 `ide_ports.json`；uninstall 脚本不再尝试释放遗留端口。`-ide-port-base` / `PROXY_IDE_PORT_BASE` 若仍设置，启动 ERROR 并忽略。
- **MITM 上 Cursor 的 `window_limited` 分支**：Cursor authorize 不再产生该状态（限额由 spend-check 在 `AgentService/Run` 前拦截），相关会话续期处理已删除；`pkcp_` OpenAI 网关不受影响。

## [0.7.0] - 2026-10-03

### 新增

- **会员与余额**：成员级会员套餐（窗口规则 + 余额模式 unlimited/prepaid）、钱包流水（充值/扣费/退还/调整）与账单（逐笔余额列、按天/按模型汇总）；团队可开启「必须开通会员才能使用」；内部 `POST /api/internal/v1/proxy/spend-check` 统一校验。
- **会员管理成员列表**：只显示会员中与已取消的成员（有过会员或余额流水，余额为 0 也保留以便查账单），可按「全部 / 会员中 / 已取消」筛选；未开通成员通过「开通会员」弹窗选择开通。
- **IDE 专用密钥（pkide_）**：控制台「复制命令 → Cursor IDE」与 `kind=ide` client-setup 签发 `pkide_`（quota 代理密钥与 `proxy_alias` 借用）；`cursor_direct` 仍交付 `cr*`。`pkide_` 仅用于 IDE `http.proxy` 归因，不能 exchange、不能给 CLI、不能走 OpenAI 网关；用量与父 key 一致。借用记录与「我的借用」可对 `proxy_alias` 活跃借用 **重置 IDE 密钥**（立即失效旧 key，复制新接入命令）。
- **借用 Key 滚动用量限制**：管理员可为 `pka_` 借用配置多条规则（滚动 5 小时 / 7 天 / 30 天，Auto 或 API，整数美元），任一达到即限制对应桶；分配和调整出借方式里用「+」添加，没有规则时不展开。代理在 `AgentService/Run` 超限返回中文 429 与恢复时间，BYOK 与 `cr*` 直连不计入。

### 变更

- **限额统一到会员**：`pk_` / `pka_` / `pkide_` 用量按成员合计；窗口规则支持 5h/7d/30d × Auto/API/合计，多条为「或」；仅在 `AgentService/Run` 前 spend-check 拦截（不再在 authorize 返回 `window_limited`）。
- **借用用量限制与 pk_ 窗口迁移**：升级时合并为成员自定义会员（同周期同桶取最小限额，`credit_mode=unlimited`）；清空借用封顶列与 Cursor `pk_` 窗口列。升级后建议管理员在「会员」中核对规则（成员范围现含名下全部 key 用量，部分成员可能立即超限）。
- **IDE 接入改为共享主端口 + userinfo**：`setup-cursor.ps1` 不再分配 `/ide-port`，将 `http://<pkide_>:x@主端口` 写入 `http.proxy`，并把 `http.proxy` 加入 `settingsSync.ignoredSettings`。拒绝在 IDE 脚本中传入 `pk_` / `pka_`。计费隧道无 key 或 userinfo 为全权 key 时返回明确 401；`pkide_` 调 exchange 返回 403。
- **移除 `-ide-pulse-key` 兜底**：`-ide-pulse-key` / `PROXY_IDE_PULSE_KEY` / 配置 `ide_pulse_key` 已删除；若仍配置则启动 ERROR 并忽略。旧版每 key 专属端口（`/ide-port`、`PROXY_IDE_PORT_BASE`）本版仍可用但标记 deprecated，**下版删除**。
- **借用记录展示用量限制**：借用记录和「我的借用」按 Auto / API 显示每条滚动限额的已用/上限和短进度，列宽固定；同一类型的多条用「或」连接；未设置显示「不限」。恢复时间在悬停里。
- **借用记录合并回收与归还**：自动回收日和归还时间收成一列两行。归还时间悬停可看精确到秒。
- **打分表去掉余量列**：入选排序不再显示 Auto 余量 / API 余量。额度列仍显示 Auto / API 用量比例，打分仍按该桶余量计算。
- **借用记录表格收窄**：自动回收日/归还时间放到创建时间后面；各列收到刚好放下内容的宽度，避免横向滚动。宽屏时这些列按比例拉满整行。
- **用量限制悬停提示**：借用记录与「我的借用」的限额 tooltip 显示滚动窗口还剩多久重置。
- **管理端页标题**：各页去掉与顶栏重复的 h2，保留说明文案与操作区。
- **额度看板隐藏已删账号**：软删除账号与 primary Key 已吊销（Key已删除）的账号不再出现在额度看板；其它列表继续按 `deleted_at` 过滤。
- **指定账号分配不再填写目标模型**：账号由管理员选定，模型名不参与发 Key。自动（账号池轮换）仍可填写，用来在确认前检查对应 Auto/API 池是否有号。

### 弃用

- **借用 `usage_caps` 与 `PATCH /api/v2/loans/{id}/usage-cap`**：非空写入返回 400「用量限制已迁移到会员，请在「会员」中设置」。
- **Cursor `pk_` 的 `window_5h_cost_usd` / `window_7d_cost_usd`**：非空写入同样 400；`pkcp_` 窗口不受影响。
- **下版删除**：内部 `/api/internal/v1/proxy/loan-usage-cap`（本版委托 spend-check，供旧 Go 代理过渡）、旧版每 key 专属 IDE 端口（`/ide-port`、`PROXY_IDE_PORT_BASE`）。请在升级到下一版前完成 Go 代理升级，并让成员重新复制 IDE 接入命令。

### 修复

- **OpenAI 网关流式用量**：`POST /openai/v1/chat/completions` 在 `stream: true` 时边转发 SSE 边解析末包 `usage`，写入 proxy 明细；请求会补上 `stream_options.include_usage`。需重新部署 Go 代理后生效。

## [0.6.0] - 2026-09-29

### 变更

- **Switch dwell 默认间隔**：`loan_selection.min_switch_minutes` 与 Go `PROXY_STICKY_MIN_DWELL` 默认值由 30 分钟改为 **20 分钟**（已保存的团队选号规则不变，需在「借用管理 → 选号规则」手动改或设环境变量）。

### 新增

- **Web 小脉流式回复**：LLM 改为 SSE 流式调用，Web 聊天边生成边显示（「小脉 · 输入中」草稿气泡）；工具仍返回 JSON 由 LLM 按技能模板排版。草稿存 `portal_chat_streams`，正式回复带 `stream_id` 时同事务替换。钉钉/飞书仍发整条。可用 `ASSISTANT_LLM_STREAM_WEB_REPLIES=false` 或团队设置 `assistant_llm.stream_web_replies` 关闭。
- **Web 小脉聊天历史**：用户消息也写入 `portal_chat_deliveries`，新增 `GET /api/chat/history`；刷新后恢复历史并续接进行中的回复。消息显示发送时间（中国时区），回复按 Markdown 渲染；输入框内悬浮发送按钮，抽屉加宽。
- **Jev 外呼耗时**：`jev_trace.meta` 记录 `called_at`（UTC ISO）与 `duration_ms`（HTTP 端到端）；缓存命中时沿用首次外呼的时间与耗时。摘要 Tab 与中国时区展示；审计 `lender_auto_pick` 附带 `jev_called_at` / `jev_duration_ms`。
- **noul 响应解析**：识别 OpenRouter 返回的 `{"type":"noul","noul":…}`，主负责人安全判定与报文表格可正确显示。
- **Jev 输入改用「在用」占座**：发给 Jev 的 `state` / `questions` 使用经代理上报的 `proxy_active_seats`（按成员计座），不再包含固定借用笔数 `active_loans`；约束改为 `max_concurrent_proxy_users`。
- **打分表 Jev 报文**：`/proxy-pool/ranking` 的 `decision.jev_trace` 返回 Decisions 请求的 `state` / `questions` 与响应 `answers`（含缓存命中时的出参快照）。借用管理 → 打分表可通过「Jev 报文」抽屉查看摘要、护栏与完整 JSON；「为成员分配 Key」选自动分配时亦可查看同一池顺序的报文。
- **Jev 强制外呼**：`GET /proxy-pool/ranking?force_jev=1`（需 `proxy:write`，30s/团队限流）与 `POST /loans/auto-pick` 的 `force_jev` 可绕过 TTL 缓存。指定账号分配时按借用人 + 目标模型调用 auto-pick 预览 Jev 报文；额度看板可打开池顺序报文。

### 修复

- **代理用量上报失败可补发**：`flushUsage` 重试失败后把批次放回缓冲区（带上限与短暂退避），不再整批丢弃。
- **代理 OpenAI 网关请求体上限**：`/openai/v1/chat/completions` 与 MITM 共用 `PROXY_MAX_BODY`（默认 32 MiB），超限返回 413。
- **代理根服务器超时**：`http.Server` 设置 `ReadHeaderTimeout` / `IdleTimeout`，与 MITM 连接一致。
- **小脉 `reply.send` 投递失败可重试**：HTTP 失败或返回非 `sent`/`skipped` 时抛错，走 job 失败重试，不再静默标 `done`。渠道去重改为「先占坑、失败释放」，避免首次失败后重试被误判为已发送。
- **长对话不被 90 s 卡死回收误重跑**：job 执行期间每约 30 s 心跳刷新 `updated_at`；`job_processing_timeout_seconds` 仍按距上次心跳计时，崩溃可及时回收。
- **安全：代理换票不再下发上游 Cursor JWT**：`pka_`/`pk_` 换票原先把 `exchange_user_api_key` 返回的真实 `accessToken` 交给客户端，借用人可直连 Cursor 调 Dashboard（含创建 Key）。现默认签发代理替身 JWT（`PROXY_OPAQUE_SESSION_TOKEN`，设 `off` 可回退），真实 token 仅留在代理内存；MITM 转发业务与 `/auth/*` 时再换成上游 JWT。
- **安全：`/admin/*` 未登录任意文件读取**：静态路由仅过滤 `..`，`/admin//etc/...` 或 `%2F` 编码的绝对路径可读取服务器任意文件。现解析后校验必须位于 SPA 目录内。升级后建议轮换 JWT 密钥、内部 service token 与 `ASSISTANT_SECRET_KEY`。
- **安全：设置密钥明文查看收紧**：`GET /api/settings/{section}/reveal/{key}` 由 `settings:read` 改为需 `settings:write`，审计员、运营员不再能查看钉钉/飞书/LLM 等密钥明文；前端无写权限时隐藏查看按钮。
- **Web 小脉显示旧回复**：`POST /api/chat` 曾固定返回 `poll_after=0`，前端从最早的投递开始轮询，重复显示历史回复并提前停止，新回复看不到。现从本次用户消息之后轮询；无回应超过 5 分钟自动解除「正在想」并提示。

## [0.5.0] - 2026-09-22

### 新增

- **同时在线人数**：Go 代理在换票、会话续期和因额度耗尽换号时，把当前连接的凭证上报给 Web。Web 按人计座（同一成员在同一个账号上的多个会话算 1 人，同时用两个账号则各占一席），默认同一个账号不超过 3 个经代理的同时使用者。没有候选账号时不算人数上限。已经在座的人不被挤走；指定账号的借用始终留在原账号。0 表示不限制。主负责人直接使用 Cursor 不计入。规则和参数在「系统设置 → 选号规则」。
- **管理员自动分配走账号池轮换**：借用记录里选「自动（账号池轮换）」会签发没有单一出借账号的 `pka_`（`routing_mode=pool`）。使用过程中与历史接入密钥共用已入池账号和打分表，确认时不锁定账号，也不新建 Cursor Key。指定账号仍固定绑定。自助申请 Key 仍是候选账号白名单游走。导航「共享池代理」改为「账号池」：默认是入池开关，打分表保留，原接入密钥收到「历史接入密钥」。
- **借用两种分配方式**：`lender_mode=manual`（指定借用）在选定账号上建一把独立 Cursor Key 并固定绑定；`lender_mode=auto`（自动分配借用）由 Auto Lender 打分决定起始账号，之后借用 Key 在候选账号的 **primary** Key 之间按共享池方式游走（per-session sticky + 30 分钟驻留 + 按 Quota Pool），换号不新建 Cursor Key。自助借 Key 默认走自动分配。
- **Jev 主判（OpenRouter Decisions）**：接入 TypeSafe System One 决策模型 `typesafe/jev-1.13`，用 `choice` 问该借哪个账号、`noul` 逐候选问是否侵占主负责人预留。它只在存活候选上重排，调用失败 / 置信度不足 / 首选与次优间隔过小 / 判定影响主负责人时一律回落算法分；带特征哈希缓存与连续失败熔断。可在「系统设置 → Jev 决策模型」配置，或走 `JEV_*` 环境变量。
- **按 Quota Pool 打分**：给出目标模型时按该模型所属桶（`auto` / `api`）取余量与空闲额度；桶由既有计费口径 `pulse/pricing/billing_scope.py` 自动判定，无需人工指定；`unknown` 退化为两桶都要有余量。
- **主负责人保留量**：账号级 `proxy_reserve_pct`（默认取 `loan_selection.owner_reserve_pct`）。号主按当前速率外推到作废日会吃掉保留量时，该账号作为出借方被硬排除（`owner_reserve`）。
- **Switch dwell 最小驻留**：`loan_selection.min_switch_minutes`（默认 30 分钟，评分侧降权）与 Go `PROXY_STICKY_MIN_DWELL`（默认 30m，请求侧不轮转；`0`/`off` 关闭）。
- **借用自动选号预览**：`POST /api/v2/loans/auto-pick` 返回打分排序 + Jev 决策；「为成员分配 Key」弹窗选自动分配时先看预计起始账号再确认。

### 变更

- **换号不再走 DB 层定时重评**：自动分配借用的换号改由代理在会话内完成（授权下发候选 primary 凭证白名单，Go 在白名单内 sticky 轮转），`reevaluate_auto_loans` 与 `auto_lender_reevaluate` 作业已移除。此前每次换号都要新建/吊销一把 Cursor Key 且粒度只能到分钟级。`reassign_loan_source` 保留为管理员手动改绑。
- **自动分配借用的消耗口径改为按 `loan_id` 归因**（`borrowed_basis=proxy`）：流量会在账号间游走，单账号快照差值不再代表本笔消耗；manual 仍是快照近似。
- **人工分统一两条路径**：`proxy_score_adjust` 此前只影响 Credential Pool 顺序、不影响借 Key 出借排序（CONTEXT.md 明确写过），现在两条路径共用同一字段与语义。
- **借用列表**：新增 `lender_mode` / `source_bound_at` / `borrowed_basis` 字段；打分表新增「主负责人保留」列与 Jev 决策提示；额度看板新增「Auto 首选」标记；打分表支持按目标模型（`?model=`）查看该桶下的顺序。
- **配置**：新增 `LoanSelectionConfig` 的 `min_switch_minutes` / `recency_penalty` / `owner_reserve_pct` / `auto_mode` / `auto_top_n` / `auto_min_confidence` / `auto_min_margin` / `auto_cache_seconds` / `auto_switch_margin`，以及 `JevConfig`。
- **迁移**：`key_loans` 增 `lender_mode`（默认 `manual`）与 `source_bound_at`（存量记录回填为 `created_at`），`ai_accounts` 增 `proxy_reserve_pct`。

### 修复

- **打分表不显示主负责人保留量**：`/proxy-pool/ranking` 的 payload 未回传生效的 `reserve_pct`，前端「主负责人保留」列永远为空。改为随打分结果一并回显（含排除项）。
- **Auto Lender 决策未落审计**：`on_decision` 钩子此前只有测试使用，生产路径没有接线。管理员发放与自助借用现在都会写 `lender_auto_pick` 事件（含来源、置信度、回落原因、选中账号）。
- **模型未知时的按池语义**：借用选号在未指定目标模型时改传 Quota Pool `unknown`（要求 auto 与 api 两桶都有余量），与 Go `snapshotQuotaOK` 一致；代理入池仍保持 CONTEXT.md 的「任一桶有余量」规则。
- **打分表两个微调互相清空**：`/proxy-pool/accounts/{id}/score` 把「未传 `score_adjust`」当成显式清空，保存「主负责人保留」会静默抹掉已有的人工分。改为按 `model_fields_set` 只更新显式传入的字段。
- **auto 模式选号误报「账号不存在」**：`loan-key` 在解析 `lender_mode` 之前先用 URL 上的 `account_id` 校验账号，前端未预选账号时发出的占位 id 会直接 404。auto 模式跳过该校验。
- **换绑/发放失败残留远端 Key**：远端 Key 已创建但本地事务失败时，只回滚数据库，Cursor 侧那条 Key 因无本地记录而无法回收。改为失败即 best-effort 吊销。
- **日趋势图例重叠**：ECharts 6 默认把 legend 放在底部，grid 底部留白不够，图例会叠在日期和矮柱上。概览 / 用量分析共用的日趋势改为顶部图例，并加大 `grid.top`。
- **概览与用量分析日趋势对齐**：两页共用同一套日聚合；日趋势改为堆叠柱（输入/输出/cache，柱高=当日总 Token）+ 花费折线，避免平滑面积图把相邻日「鼓包」。概览改为本账期全日序列，不再截成近 14 天。

### 变更（借用管理 UI）

- **借用管理合并页**：共享池代理导航改为「账号池 / 借用管理」；选号规则、打分表、借用记录集中展示；移除独立 Proxy Keys 页与设置内选号规则 Tab。
- **借 Key 通知**：钉钉/IM 下发临时 Key 时列出系统设置中全部有效代理地址及对应 PowerShell / bash 启动命令（多地址带展示名）。

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

[Unreleased]: https://github.com/cnwinds/cursor-pulse/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/cnwinds/cursor-pulse/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/cnwinds/cursor-pulse/releases/tag/v0.1.0
