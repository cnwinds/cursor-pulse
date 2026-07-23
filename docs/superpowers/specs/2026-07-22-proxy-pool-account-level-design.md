# 代理池账号级开关设计

日期：2026-07-22  
状态：已确认（用户批准方案 A）  
关联：`docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md`

## 1. 问题

当前「代理池」按 **凭证** 列表展示（`crsr_...` + `pulse-loan-*` 名称），与「借用 Key」视觉混淆。用户心智是：

1. **代理池** = 全部 Cursor **台账账号**，开关控制是否入池  
2. **脉冲 Key**（`pk_...`）= 使用方独立创建，与借用记录无关、分开展示  

## 2. 决策

| 点 | 结论 |
|---|---|
| 开关粒度 | 账号级（`AiAccount.proxy_enabled`） |
| 入池展开 | 账号开启后，其下全部 `status=active` 的 Cursor 凭证进入 Go 池 |
| 旧凭证列 | `AiAccountCredential.proxy_enabled` 保留但不再生效；迁移后以账号字段为准 |
| UI | 「代理池」Tab 一行一账号；「脉冲 Key」Tab 仅 `pk_...`；借用仍在「借用记录」 |

## 3. 数据与迁移

### 3.1 模型

`AiAccount` 增加：

```python
proxy_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
```

### 3.2 迁移

1. `ALTER TABLE ai_accounts ADD COLUMN proxy_enabled BOOLEAN DEFAULT 0`（若不存在）  
2. 回填：对每个 Cursor 账号，若存在任一凭证 `proxy_enabled=1`，则账号 `proxy_enabled=1`  
3. 不删除凭证列（兼容旧代码/测试逐步切换）

## 4. API

### 4.1 管理

- `GET /api/v2/proxy-pool/accounts` → 账号列表  
  字段：`id`, `account_identifier`, `plan_name`, `status`, `primary_member_name`, `active_credential_count`, `proxy_enabled`  
- `POST /api/v2/proxy-pool/accounts/{account_id}` body `{proxy_enabled: bool}`  
- 旧 `/api/v2/proxy-pool/credentials*`：保留实现但改为基于账号语义或标记 deprecated；前端只调 accounts

### 4.2 内部池

`GET /api/internal/v1/proxy/pool` 过滤改为：

- `AiVendor.slug == "cursor"` 且 `is_active`  
- `AiAccount.proxy_enabled == true` 且 `deleted_at is null`  
- `AiAccountCredential.status == "active"`  
- `AiAccountCredential.key_role == "primary"`（借用/loan Key **不入池**，避免与借出生命周期混用）

仍返回 `{credential_id, api_key}`，Go 数据面无需改协议。

管理列表 `active_credential_count` 仅统计 active 的 primary 凭证；列名「主 Key」。

## 5. 前端（`ProxyKeysView.vue`）

**代理池 Tab**

| 列 | 说明 |
|---|---|
| 账号 | `account_identifier` |
| 计划 | `plan_name` |
| 主责 | `primary_member_name` |
| 可用凭证 | `active_credential_count`（仅 primary） |
| 状态 | 账号 status |
| 入池 | `proxy_enabled` 开关 |

页头说明：开启后仅主 Key 入池；借用 Key 不入池。

**脉冲 Key Tab**：保持现有 CRUD；列标题「Key」改为「Key 前缀」避免误解。

## 6. 测试

- 迁移回填：凭证曾开启 → 账号开启  
- 管理 API：列表按账号；开关只改账号字段  
- 内部 pool：只下发「账号已开启」下的 active 凭证  
- 前端：调用 accounts 端点（可测 API，UI 手工冒烟）

## 7. 非目标

- 不改借用/贷款业务  
- 不删除凭证 `proxy_enabled` 列（本迭代）  
- 不做账号内「选部分凭证入池」  
