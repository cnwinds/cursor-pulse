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
5. **黏性调度（Switch dwell）**：每个 `pkcp_` 在 `loan_selection.min_switch_minutes`（默认 30 分钟）内固定同一入池账号（cache 命中）；超时后按额度压力 + 代理占座并发（`max_concurrent_users`）负载均衡。429/failover 立即换号并释放旧占座。

## 非目标（M4 MVP）

- 完整 sub2api 式多租户计费、Redis 队列、Anthropic Messages 协议。
- Kimi 上游若文档未稳定，网关对该 vendor 返回 501，池与密钥可先配置。

## 测试

- `tests/test_openai_coding_plan_proxy.py`：upstream 解析、池排序、鉴权、转发 mock。
