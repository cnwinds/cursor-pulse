# Skill 预览窗口 + 续读设计

**日期：** 2026-07-21  
**状态：** 已落地（2026-07-21）  
**范围：** 命中 Skill 注入前 N 行正文；行数元信息；`load_skill_docs` 按 `start_line` 续读  
**前置：** [2026-07-21-file-as-skill-vector-routing-design.md](./2026-07-21-file-as-skill-vector-routing-design.md)  
**非目标：** 强制「必须先 load 再调业务 tool」；改向量路由 / 授权模型

---

## 1. 动机

向量路由只注入名片时，模型常跳过 `load_skill_docs`，直接调业务 tool，展示版式等正文约束易被忽略。  
改为：命中后默认带上正文前窗口，并标明总行数 / 已读行数，使模型能判断是否续读。

## 2. 决策

| 项 | 决定 |
|----|------|
| 注入窗口 | 正文前 **200** 行（常量；可后续配置化） |
| 行计数对象 | frontmatter 之后的 **正文**（`body.splitlines()`） |
| 「适用场景」 | 仍在名片区列出；`start_line==1` 时 load/预览 markdown 可前置「适用场景」块，**不计入行号** |
| 续读 | `load_skill_docs(skill_id, start_line?, max_lines?)` |
| 短文件 | `loaded_lines == total_lines` 标明已完整载入，无需再调 |

## 3. System 注入格式

每个命中 skill：

```text
### 名称 (`skill_id`)
summary
适用场景:
- ...

<!-- skill_preview skill_id=... total_lines=N loaded_lines=M start_line=1 end_line=M -->
（正文第 1..M 行）

若 M < N：提示 load_skill_docs(skill_id, start_line=M+1) 续读。
若 M == N：提示已完整载入。
```

## 4. `load_skill_docs`

| 参数 | 含义 |
|------|------|
| `skill_id` | 必填 |
| `start_line` | 可选，默认 `1`；1-based，相对正文 |
| `max_lines` | 可选，默认 `200` |
| `section` | 忽略（兼容） |

成功返回：

```json
{
  "ok": true,
  "skill_id": "...",
  "total_lines": 520,
  "start_line": 201,
  "end_line": 400,
  "loaded_lines": 200,
  "has_more": true,
  "next_start_line": 401,
  "truncated": false,
  "markdown": "..."
}
```

- `start_line > total_lines`（且 `total_lines > 0`）→ `ok: false`
- `start_line < 1` → 钳到 `1`
- 原 `token_budget` 截断仍作兜底，`truncated=true` 时保留

## 5. Policy

- 命中 skill 已带前 200 行 + 行数元信息
- `loaded_lines < total_lines` 且需要后文时用 `start_line` 续读
- 已完整载入则不要重复调用

## 6. 会话账本

- `context` 事件的 skills 项可带 `total_lines` / `loaded_lines` / `has_more`
- 续读调用记为 `工具 · load_skill_docs`

## 7. 验收

1. 短 skill（<200 行）：system 含全文预览且 `loaded_lines == total_lines`。  
2. 长 skill（测试夹具 >200 行）：仅前 200 行；`load_skill_docs(start_line=201)` 返回后续块与 `has_more`。  
3. 回归：不可见 skill 仍拒绝；旧 `section` 参数仍可传但忽略。

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-07-21 | 初稿；用户确认方案 B（续读）后批准 |
| 2026-07-21 | 已落地：Registry 行窗口、`load_skill_docs` 续读、system 预览注入、context ledger 行数、相关测试通过 |
