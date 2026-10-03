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

## 静态检查

Python 使用 Ruff（见 `pyproject.toml`）：

```bash
ruff format pulse assistant_platform tests
ruff check pulse assistant_platform tests
```

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

启动子代理（subagent）时须遵守以下模型限制，**任何情况下**均适用（本地 Agent、Cloud Agent 等）：

- **禁止**使用 API 类模型（例如 Claude、GPT、Gemini、Muse 等通过 API 路由的模型 slug）作为子代理的工作模型。
- **仅允许**使用 **Grok**（`cursor-grok-*`）与 **Composer**（`composer-*`）系列模型。

在 Grok 与 Composer 之间选型时，在确保完成质量的前提下优先 **`composer-2.5`**（或同系列的 fast 变体）；需要更强推理或探索能力时再选用合适的 `cursor-grok-*` 档位。

## Agent skills

### Issue tracker

Issues live in GitHub (`cnwinds/cursor-pulse`). See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles with default label strings. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: root `CONTEXT.md` + `docs/adr/`. See `docs/agents/domain.md`.

### Releases

Version bumps, tags, and GitHub Releases: see `docs/agents/release.md`.

**Critical**: GitHub Release notes must be **English** (same style as `v0.2.0`); `CHANGELOG.md` remains Chinese. Do not paste the Chinese changelog into `gh release create`.
