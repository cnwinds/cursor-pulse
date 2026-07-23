# 脉冲 Key 可还原 + 一键复制客户端命令

日期：2026-07-22  
状态：已确认  
关联：`docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md`

## 1. 目标

管理员（`proxy:write`）与 Key 归属本人可还原完整 `pk_...`，前端一键复制 Linux / Windows PowerShell 启动命令，粘贴执行即可走代理。

## 2. 决策

| 点 | 结论 |
|---|---|
| 明文存储 | `proxy_keys.encrypted_key`，用 `PULSE_CREDENTIAL_ENCRYPTION_KEY` 加密 |
| 鉴权 | 仍用 `key_hash`；authorize 不变 |
| 可见范围 | `proxy:write` 可还原任意 Key；`proxy:read` 仅可还原 `member_id == 自己` |
| 创建表单 | 「名称」→「选择借用人」；`name` 自动取成员 `display_name` |
| 代理地址 | `.env` 的 `PROXY_PUBLIC_URL`（默认 `http://127.0.0.1:8317`） |
| 历史 Key | `encrypted_key` 为空 → reveal 返回 410，提示新建 |

## 3. API

- `GET /api/v2/proxy-keys/{id}/client-setup?shell=bash|powershell`
  - 鉴权：登录 + 上述可见范围
  - 200：`{ plaintext_key, proxy_url, shell, command }`
  - 403：无权限；410：不可还原；404：不存在
- 创建：`member_id` 必填；`name` 可省略（服务端填 display_name）

## 4. 命令模板

> CLI 可执行名为 `agent`（见 `2026-07-22-proxy-client-cli-agent-rename-design.md`）。

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

## 5. 前端

- 创建弹窗：成员下拉「选择借用人」
- 每行操作：「复制命令」→ 下拉选 Linux / PowerShell → 调 client-setup → 剪贴板
- 创建成功弹窗：同样提供复制命令（可直接用返回的 plaintext，少一次 reveal）

## 6. 非目标

- 不向列表接口返回明文
- 不回填历史 Key 明文
- 本迭代不做成员自助门户（本人需具备 `proxy:read` 才能进管理页）
