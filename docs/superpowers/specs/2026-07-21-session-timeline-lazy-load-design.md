# 会话账本：时间线懒加载 + 目录首问预览

**日期：** 2026-07-21  
**状态：** 已落地（2026-07-21）  
**范围：** Web Admin「会话账本」用户对话抽屉；会话列表 API 补充首条用户消息预览  
**相关：** `web-admin/src/views/SessionsView.vue`、`assistant_platform/api/sessions.py`、`pulse/web/assistant_sessions_api.py`

## 1. 背景与问题

打开某用户的对话抽屉时，`buildStreamForGroup` 会：

1. 拉取该用户全部会话列表；
2. 对每个会话并行请求详情（含全部消息）；
3. 按时间拼成连续消息流并滚到底部。

用户会话数到十几、二十时，首屏等待明显变长。真正需要的是：**先看最近对话**，更早的会话按需再加载。

同时，会话目录目前只有短 ID + 状态 + 时间，无法辨认话题，跳转价值低；需要展示**会话开始时用户第一条发送内容**（通常是首问）。

## 2. 已确认决策

| 项 | 决定 |
|----|------|
| 加载策略 | 前端窗口分页：默认最近 **2** 个会话详情；触顶再加载更早的 **2** 个 |
| 会话目录 | 始终列出该用户**全部**会话（轻量列表） |
| 目录跳转 | 目标未加载时，拉取「目标会话 → 当前已加载最早会话」之间的全部详情，再滚动定位（方案 A） |
| 首问预览 | 列表 API 增加 `first_user_text`；目录展示截断预览 |
| 后端范围 | **仅**扩展 list sessions 返回字段；不做消息流游标分页 API |
| 非目标 | 单会话内消息分页、虚拟列表、后端 timeline 聚合接口 |

## 3. 目标与非目标

### 3.1 目标

1. 打开用户对话抽屉时，首屏只请求最近 2 个会话的详情，尽快展示并滚到底部。
2. 用户将消息流滚到顶部边界时，自动加载更早的会话批次（每批 2 个），并保持阅读位置不跳动。
3. 会话目录展示全部会话 + 首问预览；点击未加载会话可按需加载并跳转。
4. 列表接口一次返回截断后的 `first_user_text`，避免目录再打 N 次详情。

### 3.2 非目标

- 改写会话详情/导出/关闭 API 契约（除 list 增字段外）
- 单会话内对 `messages` 做分页或截断
- DOM 虚拟滚动
- 为预览单独新建 HTTP 接口（在现有 list 上扩展即可）

## 4. 后端：列表补充 `first_user_text`

### 4.1 契约

`GET /api/assistant/v1/sessions`（经 `/api/v2/assistant/sessions` 透出）的每条 `items[]` 增加：

| 字段 | 类型 | 说明 |
|------|------|------|
| `first_user_text` | `string \| null` | 该会话按 `created_at` 最早的一条 `role=user` 消息的 `text_redacted`，截断至 **80** 字符；超长末尾加 `…`；无用户消息时为 `null` |

其余字段不变。v2 代理原样透传（与现有 `user_display_name`  enrichment 兼容）。

### 4.2 实现要点

- 在 list 拿到本页 `session_id` 集合后，用**一次**批量查询取各会话最早 user 消息（可用窗口函数 / 按 session 分组取 min(created_at) 再 join，或等价高效写法）。
- **禁止**对每个 session 单独 `SELECT` 全量 messages。
- 截断在服务端完成，前端直接展示。

### 4.3 测试

- 有用户消息的会话：`first_user_text` 为截断后的首条 user 文本。
- 无用户消息：`first_user_text` 为 `null`。
- 长文本：长度 ≤ 81（80 + 可能的省略号语义以实现为准，须稳定可断言）。

## 5. 前端：时间线懒加载

### 5.1 状态

在 `SessionsView.vue` 中维护（命名可微调）：

| 状态 | 含义 |
|------|------|
| `sessionCatalog` | 该用户全部会话元数据（含 `first_user_text`），按时间排序 |
| `loadedSessionIds` / 已加载详情缓存 | 已拉取过完整 messages 的会话 |
| `oldestLoadedIndex` | 当前已加载窗口在目录中的上界（更早方向） |
| `timelineLoading` | 首屏/整抽屉加载 |
| `prependLoading` | 触顶追加加载中（防抖、防重入） |

常量：`INITIAL_SESSION_BATCH = 2`，`SCROLL_SESSION_BATCH = 2`。

### 5.2 打开抽屉

1. `fetchSessionsForUser` → 写入 `sessionCatalog`（目录立即可用）。
2. 按 `opened_at || last_activity_at` **升序**排列后，取**末尾 2 个**（最近）请求详情。
3. 拼 `streamItems`（divider + messages），`scrollTop = scrollHeight`。
4. 若总会话 ≤ 2，行为与现在一致（一次加载完）。

### 5.3 触顶加载

- 在 `.chat-stream` 上监听 `scroll`：当 `scrollTop` 小于阈值（如 40px）、仍有更早未加载会话、且未在 `prependLoading` 时触发。
- 加载下一批（最多 2 个）更早会话详情，**插入**到 `streamItems` 顶部。
- 用加载前 `scrollHeight` / `scrollTop` 做位置补偿，避免视口被顶下去。
- 顶部可显示轻量「加载更早会话…」提示；不要用整抽屉 `v-loading` 盖住已有内容。

### 5.4 目录展示与跳转

目录项展示：

- 短会话 ID + 状态标签
- **首问预览**：`first_user_text`；为 `null`/空时显示「（暂无用户消息）」
- 打开/关闭时间（保持现有）

点击目录项：

1. 若目标已在已加载集合中 → 直接 `jumpToSession`。
2. 否则：计算需加载的会话集合 = 从目标（含）到当前已加载最早会话（不含，或含已加载边界）之间所有尚未加载的会话；并行拉详情；插入到流中正确时间位置；更新窗口上界；再 `jumpToSession`。
3. 跳转过程可用局部 loading，避免整页闪白。

### 5.5 关闭会话 / 刷新

关闭会话成功后：刷新列表分组；若抽屉仍打开，按当前懒加载规则重建（重新默认最近 2 个，或保留已加载集合——**采用重建为默认最近 2 个**，实现更简单、与打开行为一致）。

## 6. 错误处理

- 首屏详情失败：提示「加载对话记录失败」，目录若已有列表可保留。
- 触顶/跳转加载失败：`ElMessage.error`，不清空已加载流；允许重试（再次触顶或再次点击目录）。
- 并发：`prependLoading` / 跳转 loading 互斥或合并为单一 in-flight 锁，避免重复请求同一批。

## 7. 验收标准

1. 拥有 ≥ 3 个会话的用户：打开抽屉时网络上最多约 1 次 list + 2 次 detail（而非 N 次 detail）。
2. 滚到消息流顶部后，更早会话出现，阅读位置无明显跳动。
3. 目录可见全部会话及首问预览；点击未加载的较早会话可定位到对应 divider。
4. 会话 ≤ 2 时行为与优化前一致。
5. 现有关闭/导出仍可用。

## 8. 实现范围（文件）

| 层 | 文件 |
|----|------|
| API | `assistant_platform/api/sessions.py`（list 增字段 + 批量查首条 user） |
| 测试 | `tests/assistant_platform/test_sessions_api.py`（及必要时 v2 透传断言） |
| Admin UI | `web-admin/src/views/SessionsView.vue` |

v2 代理若只透传 JSON，通常无需改逻辑；若有字段白名单则需放行 `first_user_text`。
