# 代理客户端 CLI：`cursor-agent` → `agent`

日期：2026-07-22  
状态：已确认（用户批准方案 A，直接实施）  
关联：`docs/superpowers/specs/2026-07-22-proxy-key-reveal-command-design.md`

## 1. 问题

实际 Cursor Agent CLI 可执行名为 `agent`，一键复制命令与文档仍写 `cursor-agent -k`，粘贴后无法直接运行。

## 2. 决策（方案 A）

| 点 | 结论 |
|---|---|
| CLI 名 | 一律改为 `agent`；保留 `-k` |
| 环境变量 | 不变：`HTTPS_PROXY`、`CURSOR_API_KEY` |
| UI | 「复制命令」下拉与文案不变，仅剪贴板内容变 |
| 历史 plan/spec | **不改**（已确认历史记录） |
| 包装脚本文件名 | **不改** `cursor-agent-proxy.ps1`；只改脚本内调用 |

## 3. 命令模板（新）

PowerShell：

```powershell
$env:HTTPS_PROXY = "<PROXY_PUBLIC_URL>"
$env:CURSOR_API_KEY = "<pk_...>"
agent -k
```

bash：

```bash
export HTTPS_PROXY="<PROXY_PUBLIC_URL>"
export CURSOR_API_KEY="<pk_...>"
agent -k
```

## 4. 改动清单

| 文件 | 改动 |
|---|---|
| `pulse/proxy/service.py` | `build_client_command` 末行 `agent -k` |
| `web-admin/src/views/ProxyKeysView.vue` | 前端兜底模板同步 |
| `tests/test_web_proxy_admin.py` | 断言 `command` 含 `agent -k`、不含 `cursor-agent` |
| `docs/superpowers/specs/2026-07-22-proxy-key-reveal-command-design.md` | §4 模板同步 |
| `proxy/README.md` | 用法示例改为 `agent` |
| `proxy/main.go` | 启动提示改为 `agent` |
| `proxy/cursor-agent-proxy.ps1` | 注释与 fallback 可执行名改为 `agent`（优先 PATH 的 `agent`） |

## 5. 非目标

- 不重命名 `cursor-agent-proxy.ps1`
- 不改历史 `docs/superpowers/plans/*` 与 integration 总设计里的旧表述
- 不做可配置 CLI 名
