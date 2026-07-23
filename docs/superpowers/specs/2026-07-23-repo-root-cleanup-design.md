# 仓库根目录大扫除设计

**日期：** 2026-07-23  
**状态：** 已确认  
**范围：** 目录归位、垃圾清理、Cursor 旧用量 CSV 样本遗产清理（保留非 Cursor 手工 CSV/XLSX）

## 背景

GitHub/GitLab 根目录出现明显垃圾与重复物：空文件 `=`、与 `docker/` 重复的旧 Docker 文件、过程产物 `.superpowers/sdd/` 误跟踪、过时的 `samples/`（Cursor 用量 CSV，已被 API 同步取代）、孤立的 `review/`。根目录还应收纳错位的参考文档与脚本。

## 目标

1. 根目录只保留项目元数据、本地启动器与业务顶层目录。
2. Docker 相关文件以 `docker/` 为唯一权威位置。
3. 删除 Cursor 旧 CSV 样本及仅依赖该样本的测试；保留非 Cursor 手工 CSV/XLSX 能力。
4. 一次变更后文档链接正确、测试可绿。

## 非目标

- 不删除非 Cursor 的 `manual_csv` / `csv_parser` / 钉钉收 CSV·XLSX / `pulse parse|import` CLI。
- 不移动 `cursor-pulse.bat` / `.ps1` / `.sh`（留在根目录）。
- 不重构业务代码架构（除因删样本而必须的测试改写）。
- 不改 Docker 运行时行为（只删根目录过时副本）。

## 目标根目录

保留：

| 类型 | 路径 |
|------|------|
| 元数据 | `.env.example`、`.gitignore`、`.gitlab-ci.yml`、`README.md`、`config.example.yaml`、`pyproject.toml` |
| 启动器 | `cursor-pulse.bat`、`cursor-pulse.ps1`、`cursor-pulse.sh` |
| 业务目录 | `assistant_platform/`、`docker/`、`docs/`、`proxy/`、`pulse/`、`tests/`、`web-admin/` |

（本地未跟踪物如 `.env`、`config.yaml`、`.venv/`、`data/` 仍由 `.gitignore` 忽略，不纳入本次提交。）

## 变更清单

### 删除

| 路径 | 原因 |
|------|------|
| `=` | 空垃圾文件（误创建） |
| `Dockerfile`、`.dockerignore`、`docker-compose.yml`、`docker-compose.postgres.yml`（根目录） | 过时副本；权威在 `docker/` |
| `review/` | 一次性架构笔记，用户要求删除 |
| `samples/` | Cursor 旧用量 CSV，已被 API 取代 |
| `.superpowers/sdd/task-5-report.md`（若仍被跟踪） | 过程产物；目录已在 `.gitignore` |
| 仅断言 Cursor 样本契约的测试用例/文件 | 如基于 498 行样本金额/行数的断言 |

### 搬迁

| 从 | 到 |
|----|----|
| `cursor-usage-api.md` | `docs/cursor-usage-api.md` |
| `cursor-usage.sh` | `scripts/cursor-usage.sh` |

搬迁后更新仓库内相对链接（含历史 plan/spec 中的引用，至少更新仍会点击的活文档：`docs/plans/...`、文档自身内的脚本路径说明）。

### 文档同步

- `README.md`：去掉 `samples/` 的 `pulse parse|import` 示例；Docker 指引明确走 `docker/`。
- `docs/RUNBOOK.md`、`docs/PRD.md`：去掉 `samples/` 路径与目录树中的 `samples/` 条目（PRD 历史勾选可不强行改语义，但路径引用须失效或改写）。
- `docs/cursor-usage-api.md`：脚本路径改为 `scripts/cursor-usage.sh`。
- `proxy/README.md` / `docker/README.md`：启动器仍指向根目录 `cursor-pulse.*`（无需改路径）。

### 测试策略

依赖 `samples/usage-events-sample.csv` 的测试分两类处理：

1. **纯样本契约**（行数 498、固定 cost 分布等）→ **删除**。
2. **用样本当种子测聚合/查询/报告/拆分等业务逻辑** → **改写**：测试内最小 CSV 字符串（`StringIO` / tmp 文件）或直接写库；不再读 `samples/`。

保留并继续覆盖：非 Cursor 手工 CSV/XLSX 解析与钉钉附件相关测试（若其唯一数据源是旧 Cursor 样本，则改为迷你 fixture，而不是删能力）。

已知受影响测试文件（实现时以 `rg samples/|usage-events-sample` 复核为准）：

- `tests/test_csv_parser.py`
- `tests/test_xlsx_parser.py`
- `tests/test_aggregator.py`
- `tests/test_account_pick.py`
- `tests/test_llm_usage_query.py`
- `tests/test_period_split.py`
- `tests/test_pricing_estimator.py`
- `tests/test_query.py`
- `tests/test_report.py`
- `tests/test_split_submission.py`
- `tests/test_summary_split.py`

## Docker 权威位置

- 构建与编排：仅使用 `docker/Dockerfile`、`docker/docker-compose.yml`、`docker/docker-compose.postgres.yml`。
- 根目录不再提供 compose 入口；文档写明在 `docker/` 下执行 `docker compose ...`（与现有 `docker/README.md` 一致）。
- 不新增根目录 → `docker/` 的软链或包装脚本（避免再次双源）。

## 验收标准

1. `git ls-files` 根目录无 `=`、无根级 Docker 四件套、无 `samples/`、无 `review/`、无误跟踪的 `.superpowers/**`。
2. `docs/cursor-usage-api.md` 与 `scripts/cursor-usage.sh` 存在且旧根路径不存在。
3. `rg "samples/|usage-events-sample"` 在代码与活文档中无有效引用（历史归档 plan 可保留原文，活文档必须更新）。
4. `pytest --tb=short -q` 通过。
5. README 快速开始不再引导用户使用已删样本。

## 风险与回滚

| 风险 | 缓解 |
|------|------|
| 外部脚本仍 `docker compose -f docker-compose.yml`（根） | README/`docker/README` 写明新路径；属破坏性清理，接受 |
| 删样本后测试覆盖下降 | 对业务逻辑测试补迷你 fixture，不全删文件 |
| 链接失效 | 搬迁后全库搜旧文件名并更新 |

回滚：单次 commit/PR 还原即可。
