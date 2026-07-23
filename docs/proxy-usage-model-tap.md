# Go 代理：用量上报里的 model 怎么来的

日期：2026-07-22（2026-07-23 补充：只传线协议 model id）  
相关代码：`proxy/model_tap.go`  
诊断开关：`.env` 中 `PROXY_DEBUG_USAGE=1`（落盘目录见 `PROXY_DEBUG_USAGE_DIR`）

## 约定（A：只保证新数据）

- 上报与入库字段 `model` **只存 Cursor 线协议 model id**（如 `claude-opus-4-8`、`composer-2.5-fast`）。
- UI 展示名（「Opus 4.8」）不入库、不参与计价匹配。
- 抽不到则传空字符串；**不**回填历史错误行（例如曾误存的 `opus`）。

## 背景

`TurnEndedUpdate` 只有 token 计数，没有模型名。用量里的 `model` 只能从 **AgentService/Run 请求体** 旁路抽取。

曾用「关键词命中后取最短字符串」会误选目录里的短 id（如 `gpt-5.2`、`grok-4.5`），也曾误把 skills 路径当成模型，或把裸标签 `opus` 当成 id。下面规则来自实抓请求对照。

## 实抓样本

| 文件 | 用户所选 | 正确结果（model id） |
|---|---|---|
| `.dev/proxy-debug-usage/20260722-092843-0001.*` | Composer 2.5 Fast | `composer-2.5-fast` |
| `.dev/proxy-debug-usage/20260722-093126-0002.*` | Opus 4.8 | `claude-opus-4-8` |

## 请求体里字符串的大致顺序

Connect/protobuf 可打印字符串一般分三段：

1. 用户消息、session、工作区路径、skills 路径  
2. **当前选型**（靠前、短）：`model_id` + 参数（`fast` / `thinking` / `effort` …）  
3. **模型目录等噪声**（一长串其它模型 id）

### Composer 2.5 Fast

```
… skills / UUID …
composer-2.5    ← 选中 id
fast
true
default
gpt-5.2 / composer-2.5-fast / …  ← 目录噪声
```

### Opus 4.8

```
claude-opus-4-8 ← 选中 id（不是展示名 "Opus 4.8"，也不是裸标签 "opus"）
thinking / true / effort / high / fast / false / …
grok-4.5 / composer-2.5 / …  ← 噪声
```

## 现行抽取规则

实现见 `pickSelectedModel` / `looksLikeModelID`：

1. 按 protobuf 字符串**出现顺序**扫描  
2. 取**第一个**像模型 id 的字符串作为 `base`  
3. 在遇到下一个模型 id 之前，若出现 `fast` + `true`，结果为 `base-fast`；`fast` + `false` 不拼  
4. 抽不到则空字符串（不影响转发）  
5. 带 `/`、`\` 的路径一律丢弃（`accounts/` 除外）  
6. 候选串不得含空白（故展示名 `opus 4.8` 不会进候选）  
7. 拒绝裸家族标签：`opus` / `sonnet` / `haiku` / `claude` / `grok` / …  
8. 其余命中关键词的串须含版本数字（`cursor-small` / `cursor-fast` 除外）

## 如何再验证

1. `.env` 打开 `PROXY_DEBUG_USAGE=1`  
2. 重启 proxy，用 agent 跑一条目标模型  
3. 看 `.dev/proxy-debug-usage/*.txt` 的 `picked=` 是否为线协议 id，且选型段仍在「目录」之前  
4. 稳定后关掉诊断开关  
5. 单测：`cd proxy && go test -run Model ./...`（含上述两份 `.bin` 金标）

## Token 口径（TurnEnded → 入库）

Go 旁路抽出的五元组原样上报；Pulse `canonical_turn_ended_tokens` 再规整：

| TurnEnded | 上报 JSON | 入库含义 |
|---|---|---|
| field1 | `input` | 常为 **含 cache 的 input 合计**；入库拆成 `tokens_input = no_cache` |
| field2 | `output` | `tokens_output` |
| field3 | `cache_read` | `tokens_cache_read` |
| field4 | `cache_write` | `tokens_cache_write` |
| field5 | `reasoning` | `tokens_reasoning`（官方 API 无独立字段） |

规则：若 `input >= cache_read + cache_write` 且 cache 非零，则  
`no_cache = input - cache_read - cache_write`；否则视 `input` 已是 no_cache。  
`total_tokens = no_cache + output + cache_read + cache_write + reasoning`。  
费用为本地价表**估算**，不是 Cursor `chargedCents`。

历史行回算：`python -m pulse.cli reprice-proxy`（可选 `--loan-id` / `--proxy-key-id`）。

## 全项目费用计算路径（审计）

| 路径 | 入口 | 是否受 TurnEnded 双计影响 | 状态 |
|---|---|---|---|
| Proxy 写入 | `record_usages` → `canonical` → `estimate_cost_cents` | 是（源头） | 已修；`estimate_cost_cents` 强制 canonical |
| Proxy 历史回算 | `reprice_proxy_usages` / CLI `reprice-proxy` | 是 | 已提供 |
| Proxy 聚合/限额 | `total_usage` / `loan_proxy_totals` / `evaluate_key` | 间接（读已存 cost） | 依赖入库/回算 |
| Proxy API/UI | `proxy_keys_api` / `quota_api` / LoansView / ProxyKeysView | 间接 | 只展示，不重算；文案标注「估算」 |
| 官方同步 | `map_usage_event` → `resolve_cost_fields` | 否（字段已 split） | 独立口径，用 `chargedCents` |
| 官方 Included 估算 | `estimate_event_record` / CLI `reprice` | 否 | 入参已是 no_cache/cache_* |
| 借用近似消耗 | `borrowed_cents`（快照差值） | 否 | 非 token 价表 |
| 燃尽 | `burn_rate` | 否 | 账号快照 |

价表公式唯一实现：`pulse/pricing/types.py` → `estimate_token_cost`  
（`input_no_cache + cache_write + cache_read + output` 分段费率）。  
`gpt-5.6-sol*` 独立 Sol 档（$5 / $6.25 / $0.5 / $30），排在 `gpt-*` Codex 之前。

## 已知局限

- 不拼 `thinking` / `effort` / `context` 到展示名  
- 未按完整 protobuf schema 解析，协议大改时需重新对照 dump  
- 历史错误 `model` 行（裸 `opus`）不会自动改名；token/费用可用 `reprice-proxy` 回算  
- Proxy 费用仍是本地估算，不含官方 included / chargedCents 语义  
