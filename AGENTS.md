# Agent 约定

## 开发环境

本仓库使用项目根目录下的 `.venv` 作为 Python 虚拟环境。执行 Python 命令、跑测试、运行脚本时，请使用该环境，勿用系统全局 Python。

```bash
source .venv/bin/activate   # macOS/Linux
# Windows: .venv\Scripts\activate
```

未激活时也可直接调用：

```bash
.venv/bin/python ...
.venv/bin/pytest ...
```

若 `.venv` 不存在，按根目录 [README.md](README.md) 的「本地开发启动」创建并安装依赖。

## 测试

跑测试时默认使用并行，以缩短全量耗时：

```bash
pytest -n auto --tb=short -q
```

需要单进程调试（例如排查并发相关失败）时再用：

```bash
pytest --tb=short -q
```

## Subagent

当模型需要启动 subagent 时，在确保完成质量的情况下优先使用 **`composer-2.5`**高性价比模型，否则根据工作复杂度选用高级模型。

## Agent skills

### Issue tracker

Issues live in GitHub (`cnwinds/cursor-pulse`). See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles with default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.