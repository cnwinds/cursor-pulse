# Go 代理出站上游代理（翻墙）

日期：2026-07-22  
状态：已确认（用户批准方案 A，直接实施）  
关联：`docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md`

## 1. 目标

Go 数据面访问 Cursor（`*.cursor.sh`）时可经可配置的 HTTP(S) 上游代理出站，以支持需翻墙的模型；Pulse 控制面仍直连。

## 2. 决策

| 点 | 结论 |
|---|---|
| 配置 | `.env` / 环境变量 `PROXY_UPSTREAM_URL`；空=直连 |
| 格式 | `http://host:port` 或 `http://user:pass@host:port`（`https://` 代理亦允许） |
| CLI | 可选 `-upstream-proxy` 覆盖 env |
| 作用 | 池换票 client + MITM 出站 Transport |
| 不走 | Pulse 内部 API（`PULSE_BASE_URL`） |
| 禁止 | 不读取 `HTTPS_PROXY`（避免与客户端变量/自环混淆） |
| 日志 | 启动打印启用状态；有账号时脱敏为 `http://***@host:port` |

## 3. 非目标

- 按模型分流、管理 UI、SOCKS5（后续可加）
