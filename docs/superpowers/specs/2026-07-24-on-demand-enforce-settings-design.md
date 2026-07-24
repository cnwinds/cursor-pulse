# On-Demand 强制关闭 · Web 可配置设计

日期：2026-07-24  
状态：已评审（对话确认）

## 背景

共享 Cursor 账号若未关闭 On-Demand Spending，套餐用尽后会按量扣费。  
系统已在用量同步时调用 `GetHardLimit` / `SetHardLimit` 强制关闭；通知原先写死发给 `DINGTALK_ADMIN_USER_IDS`，且无 Web 开关。  
现要求：在 Web 后台可配置是否强制关闭、通知谁、是否通知主使用人。

## 目标

1. 团队级配置：同步时是否强制关闭 On-Demand（默认开）。
2. 关闭成功/失败时，钉钉私聊可配置的成员列表（多选）。
3. 可选：同时通知该账号主使用人（`primary_member`）。
4. 未配置通知名单时，回落到当前管理员对应成员；用户保存后以保存值为准（含空列表）。

## 非目标

- 按账号白名单/黑名单豁免强制关闭。
- 在设置页维护管理员环境变量本身。
- Cookie 网页接口路径（仍走 `api2` + User API Key）。

## 界面

位置：**系统设置 → 收集与调度 → Cursor账号同步**（复用现有编辑对话框）。

```
启用同步
巡检间隔 / 账号同步间隔
───
On-Demand 强制关闭          [switch]
关闭时通知这些人            [多选成员，可搜索]
同时通知主使用人            [switch]
```

- 强制关闭为关时：通知相关控件隐藏或禁用（`showWhen`）。
- 多选展示 `display_name`，存储 `member_id[]`。
- 列表摘要示例：`每 2 分钟巡检 · 强制关 On-Demand · 通知 2 人+主使用人`。

## 配置模型

挂在团队设置 section `cursor_sync`（`TeamSetting` deep-merge 覆盖 `config.yaml`）：

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enforce_on_demand_disabled` | bool | `true` | 同步时是否强制关闭 |
| `on_demand_notify_member_ids` | `string[]` \| unset | unset → 回落管理员 | 钉钉私聊收件人 |
| `on_demand_notify_primary` | bool | `true` | 是否通知账号主使用人 |

`CursorSyncConfig`（`pulse/config.py`）增加对应字段；`EDITABLE_SECTIONS` 已含 `cursor_sync`，无需新 section。

### 默认 / 回落规则

1. `on_demand_notify_member_ids` **未写入**（`null` / 缺省）：  
   - 前端首次打开编辑框：预填管理员对应 `member_id`（由 `admin.dingtalk_user_ids` 匹配 `Member.dingtalk_user_id`）。  
   - 运行时通知：同样回落管理员成员。
2. 用户 **已保存**（含保存为空数组 `[]`）：以库内值为准，不再自动塞回管理员。
3. 主使用人与名单重合：去重后只发一条。

区分「未配置」与「配置为空」：未配置用缺省/省略字段；空列表显式存 `[]`。

## 数据流

```
GET /api/settings
  → cursor_sync 含上述字段（有效配置）

PATCH /api/settings/cursor_sync
  → patch_team_setting 写库

sync_account
  → if enforce_on_demand_disabled:
       GetHardLimit → 必要时 SetHardLimit
  → if status in (disabled_now, disable_failed) and 需要通知:
       recipients = configured_member_ids ∪ (primary if flag)
       按 dingtalk_user_id 私聊；失败仅打日志
```

手动同步（web / 钉钉命令）同样尊重 `enforce_*`；通知通道能拿到 messenger 时发送，否则至少打日志（scheduler 路径必发）。

## 后端要点

- `pulse/ingestion/on_demand.py`：保持 enforce 纯函数；通知拼接收件人抽到 sync / sync_tick。
- `run_sync_tick` / `CursorSyncService`：读取 `effective_config.cursor_sync`，不再写死 `admin.dingtalk_user_ids` 为唯一收件人。
- 无 `dingtalk_user_id` 的成员跳过并 warning。
- 强制关闭失败仍不阻断用量入库（既有约定）。

## 前端要点

- `SettingsView.vue`：`ITEM_PLANS.cursor_sync_tick` 与 `FIELD_DEFS` 增加三字段。
- 成员多选：加载活跃成员列表（与账号台账 `el-select` 同源 API），`multiple` + `filterable`。
- `SettingsSectionForm` 若尚无 multi-member 类型，扩展一种 field type（或本对话框内联 select）。

## 测试

- 配置关 `enforce`：sync 不调用 SetHardLimit。
- 配置开 + 指定 member_ids：notify 只打这些 userid。
- `notify_primary`：主使用人收到；与名单重复不双发。
- 未配置 member_ids：回落管理员；保存 `[]` 后不再回落。
- 前端：字段出现在对话框；强制关闭关时通知字段隐藏。

## 风险

- `SetHardLimit` 为非官方 API，可能变更。
- 成员离职后 id 仍留在列表：发送时跳过即可，无需强制清理。
