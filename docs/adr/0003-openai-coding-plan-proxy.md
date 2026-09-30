# ADR 0003：Coding Plan OpenAI 兼容代理（M4）

- **状态**：**M4 已落地**（网关 failover、用量、Admin API + 借用管理 UI）
- **关联**：[ADR 0002](./0002-glm-account-quota-integration.md) M4 行；[coding-plan-quota-apis.md](../coding-plan-quota-apis.md)
- **参考**：[Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)（多账号调度、OpenAI 协议面、与 Cursor/Claude MITM 分离的产品形态）

## 背景

Cursor Pulse 已有 **Go MITM** 数据面（`proxy/`），专用于 Cursor CLI：拦截 `exchange_user_api_key`、Credential Pool、Quota Pool（auto/api）。GLM / MiniMax / Kimi **Coding Plan** 账号走额度快照，不应混入该池。

M4 增加 **独立 OpenAI Chat Completions 网关**：客户端配置 `base_url` 指向 Pulse，Bearer 使用 **`pkcp_`** 密钥；Pulse 从 **Coding Plan 专用池** 选账号，将请求转发到厂家 OpenAI 兼容 upstream。

## 与 sub2api 的对照（借鉴点）

| sub2api | Pulse M4 |
|---------|----------|
| 多订阅账号 + 分组/渠道 | `AiAccount.cp_proxy_enabled` + vendor slug |
| 用户 API Key + 计费/限流 | `ProxyKey`（`pkcp_`）+ 现有 window 限额 |
| OpenAI `/v1/chat/completions` 入口 | Go `:8317` → `POST /openai/v1/chat/completions` |
| 调度/ failover | 按额度快照 `total_pct` 升序；429/502/503 换号重试 |
| 独立于 Cursor MITM 路径 | 同 Go 进程；**不走 CONNECT**，仅 plain HTTP `/openai/v1/*` |

## 决策

1. **密钥前缀 `pkcp_`**，`ProxyKey.mode=coding_plan`，`coding_plan_vendor` ∈ {glm, minimax, kimi}。
2. **入池开关 `cp_proxy_enabled`**（账号级），与 Cursor `proxy_enabled` **互斥语义**（CP 账号仍保持 `proxy_enabled=false`）。
3. **Upstream** 按 vendor + `api_region` 解析（见 `pulse/openai_proxy/upstream.py`）；GLM 优先 **Coding Plan** base（z.ai `api/coding/paas/v4`）。
4. **数据面在 Go 代理（层 A）**：客户端 `base_url={PROXY_PUBLIC_URL}/openai/v1`；Go 经 **internal API** 向 Pulse 解析 `pkcp_`、选池、上报用量。Pulse Web **仅控制面**（Admin API + `/api/internal/v1/openai-proxy/*`），**不**再公网暴露 `/openai/v1`。
5. **黏性调度（Switch dwell）**：每个 `pkcp_` 在**两次请求间隔**小于 `loan_selection.min_switch_minutes`（默认 20 分钟）时固定同一入池账号（cache 命中）；间隔超过阈值后按打分（100 − 额度压力）+ 代理占座并发（`max_concurrent_users`）负载均衡。429/failover 立即换号并释放旧占座。
6. **占座时长**：resolve 成功即占座，调用进行中不过期；Go 在请求结束后调 `/api/internal/v1/openai-proxy/end`（失败重试），座位再保留 `min_switch_minutes`（与 dwell 对齐，同时刷新黏性绑定时间）。异常兜底：
   - **Go 重启/崩溃**：每个 Go 进程启动时生成 boot id，随所有内部调用带 `X-Proxy-Boot`；60s 一次的池轮询即心跳。某 boot 超过 180s 无调用视为已退出，它名下进行中的调用按最后出现时间结束，再保留 dwell。多个 Go 实例互不影响。
   - **调用方中途断开**：Go 仍读完上游（账号仍在消耗），读完后照常发结束通知。
   - **Pulse Web 重启**（内存占座清空）：首次 resolve / 管理列表时按 `cp_openai_sticky_bindings.updated_at` 回填 dwell 内的座位；结束通知在绑定仍指向该凭证时补建座位，换号后的旧凭证不补建。
   - **上游挂死 / 结束通知始终丢失**：进行中座位最多 2 小时。

## 非目标（M4 MVP）

- 完整 sub2api 式多租户计费、Redis 队列、Anthropic Messages 协议。
- Kimi 上游若文档未稳定，网关对该 vendor 返回 501，池与密钥可先配置。

## 测试

- `tests/test_openai_coding_plan_proxy.py`：upstream 解析、池排序、鉴权、转发 mock。
