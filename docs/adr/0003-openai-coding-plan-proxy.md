# ADR 0003：Coding Plan OpenAI 兼容代理（M4）

- **状态**：M4 进行中（控制面 + Pulse 内嵌网关 MVP）
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
| OpenAI `/v1/chat/completions` 入口 | `POST /openai/v1/chat/completions` |
| 调度/ failover | MVP：按额度快照 `total_pct` 升序；后续 sticky / 429 换号 |
| 独立于 Claude Code MITM | 不修改 Go `cursor-pulse-proxy`；HTTP 反代在 Python |

## 决策

1. **密钥前缀 `pkcp_`**，`ProxyKey.mode=coding_plan`，`coding_plan_vendor` ∈ {glm, minimax, kimi}。
2. **入池开关 `cp_proxy_enabled`**（账号级），与 Cursor `proxy_enabled` **互斥语义**（CP 账号仍保持 `proxy_enabled=false`）。
3. **Upstream** 按 vendor + `api_region` 解析（见 `pulse/openai_proxy/upstream.py`）；GLM 优先 **Coding Plan** base（z.ai `api/coding/paas/v4`）。
4. **首版范围**：Pulse Web 内嵌转发 + 管理 API；Go 代理 **不** 承载 CP 流量。

## 非目标（M4 MVP）

- 完整 sub2api 式多租户计费、Redis 队列、Anthropic Messages 协议。
- Kimi 上游若文档未稳定，网关对该 vendor 返回 501，池与密钥可先配置。

## 测试

- `tests/test_openai_coding_plan_proxy.py`：upstream 解析、池排序、鉴权、转发 mock。
