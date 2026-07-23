# Cursor 代理 · 部署挂载与联调（计划 3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Go 数据面挂入 `cursor-pulse` 开发启停体系，保证 `start/stop/restart/status/log proxy` 可用；提供构建兜底与最小冒烟。

**Architecture:** 复用 `pulse/dev/services.py` + `DevManager`；`proxy` 作为独立服务监听 `127.0.0.1:8317`，子进程继承 `.env`（含 `PULSE_BASE_URL` / `PULSE_INTERNAL_SERVICE_TOKEN`）。不改 Go 代理业务逻辑。

**Tech Stack:** Python DevManager / Go 二进制 / PowerShell+bash 包装脚本 / pytest。

**Spec:** `docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md` §11-3

**约定：**
- `proxy` **不**加入 `DEFAULT_SERVICES`（无 Go 环境时默认 `start` 不失败）；显式 `start proxy` / `restart proxy`。
- 二进制路径：`proxy/cursor-pulse-proxy.exe`（Windows）或 `proxy/cursor-pulse-proxy`（Unix）；缺失时用本机 `go build` 生成。
- 每个 Task 结束后提交。

---

### Task 1: DevService 注册 `proxy`

**Files:**
- Modify: `pulse/dev/services.py`
- Modify: `tests/test_dev_manager.py`
- Modify: `pulse/dev/__main__.py`（若 choices 从 SERVICES 派生则自动；否则补 `proxy`）

- [x] **Step 1: 失败测试**

在 `tests/test_dev_manager.py` 追加：

```python
def test_services_includes_proxy_not_default():
    assert "proxy" in SERVICES
    assert SERVICES["proxy"].port == 8317
    assert "proxy" not in DEFAULT_SERVICES


def test_build_command_proxy(tmp_path, monkeypatch):
    from pulse.dev import services as svc

    fake = tmp_path / ("cursor-pulse-proxy.exe" if __import__("sys").platform == "win32" else "cursor-pulse-proxy")
    fake.write_bytes(b"x")
    monkeypatch.setattr(svc, "ensure_proxy_binary", lambda root=None: fake)
    command, cwd, extra = svc.build_command("proxy")
    assert str(fake) in command[0] or command[0] == str(fake)
    assert "-listen" in command
    assert "127.0.0.1:8317" in command
    assert cwd == svc.project_root() / "proxy" or cwd == svc.project_root()
```

（若 `ensure_proxy_binary` 返回 Path，按实现微调断言。）

- [x] **Step 2: 跑测确认失败**

Run: `python -m pytest tests/test_dev_manager.py::test_services_includes_proxy_not_default -v`
Expected: FAIL

- [x] **Step 3: 实现**

`pulse/dev/services.py`：

1. 增加：

```python
def proxy_binary_name() -> str:
    return "cursor-pulse-proxy.exe" if sys.platform == "win32" else "cursor-pulse-proxy"


def ensure_proxy_binary(root: Path | None = None) -> Path:
    """Return path to built proxy binary; build with `go` if missing."""
    root = root or project_root()
    proxy_dir = root / "proxy"
    binary = proxy_dir / proxy_binary_name()
    if binary.exists():
        return binary
    go = shutil.which("go")
    if not go:
        # Common Windows install path
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Go" / "bin" / "go.exe"
        go = str(candidate) if candidate.exists() else None
    if not go:
        raise FileNotFoundError(
            "未找到 Go 代理二进制且未安装 go。请安装 Go 1.22+ 后执行: "
            f"cd proxy && go build -o {proxy_binary_name()} ."
        )
    import subprocess

    subprocess.run([go, "build", "-o", str(binary), "."], cwd=str(proxy_dir), check=True)
    return binary
```

（`import os` 加到文件顶部。）

2. `build_command` 增加：

```python
    if service == "proxy":
        binary = ensure_proxy_binary(root)
        return (
            [str(binary), "-listen", "127.0.0.1:8317"],
            root / "proxy",
            {},
        )
```

3. `SERVICES` 增加：

```python
    "proxy": DevService("proxy", "Cursor 代理（Go 数据面）", 8317, "http://127.0.0.1:8317"),
```

- [x] **Step 4: 跑测通过**

Run: `python -m pytest tests/test_dev_manager.py -q`
Expected: PASS（更新 `test_default_services`：`set(SERVICES)` 含 proxy；DEFAULT 仍四元组）

同步改 `test_default_services`：

```python
def test_default_services():
    assert DEFAULT_SERVICES == ("web", "admin", "channel", "assistant")
    assert set(SERVICES) == {"web", "admin", "channel", "assistant", "proxy"}
```

- [x] **Step 5: 提交**

```bash
git add pulse/dev/services.py tests/test_dev_manager.py
git commit -m "feat(dev): register Go cursor proxy as optional dev service"
```

---

### Task 2: 包装脚本允许 `proxy`

**Files:**
- Modify: `cursor-pulse.ps1`
- Modify: `cursor-pulse.sh`

- [x] **Step 1: 更新服务白名单与帮助**

`cursor-pulse.ps1`：所有 `@("web", "admin", "channel", "assistant")` 改为含 `"proxy"`；帮助文案增加：

```
  proxy     Cursor 代理（Go）     http://127.0.0.1:8317  （需 Go；不在默认 start 集合）
```

并说明：`cursor-pulse start proxy`（建议先 `start` 起 web，再起 proxy）。

`cursor-pulse.sh`：`usage` 与 case 中的服务名同样加入 `proxy`。

- [x] **Step 2: 提交**

```bash
git add cursor-pulse.ps1 cursor-pulse.sh
git commit -m "feat(dev): allow start/stop/log proxy in cursor-pulse scripts"
```

---

### Task 3: 冒烟清单 + README 交叉引用

**Files:**
- Modify: `proxy/README.md`（指向 `cursor-pulse start proxy`）
- Create: `docs/superpowers/plans` 勾选 / 或在 README 加「开发挂载」小节即可

- [x] **Step 1: README 增加开发挂载**

在 `proxy/README.md` 增加：

```markdown
## 开发环境挂载（cursor-pulse）

```powershell
.\cursor-pulse.bat start              # web/admin/channel/assistant
.\cursor-pulse.bat start proxy        # Go 数据面 :8317（首次自动 go build）
.\cursor-pulse.bat status
.\cursor-pulse.bat log proxy -f
.\cursor-pulse.bat stop proxy
```

依赖 `.env` 中的 `PULSE_BASE_URL` 与 `PULSE_INTERNAL_SERVICE_TOKEN`（DevManager 会注入子进程）。
```

- [x] **Step 2: 本地冒烟（可选）**

```powershell
.\cursor-pulse.bat start proxy
.\cursor-pulse.bat status   # proxy running, port 8317
.\cursor-pulse.bat stop proxy
```

- [x] **Step 3: 提交**

```bash
git add proxy/README.md
git commit -m "docs(proxy): document cursor-pulse proxy service mount"
```

---

### Task 4: 回归

- [x] **Step 1:** `python -m pytest tests/test_dev_manager.py -q` PASS
- [x] **Step 2:** `cd proxy && go test ./... -count=1` PASS
- [x] **Step 3:** 勾选本计划全部 Step 并提交 `chore(proxy): mark deploy mount plan complete`

---

## 自审

- Spec §11-3 覆盖：bat/sh 挂代理 → Task 1–2；联调说明 → Task 3。完整「假上游 E2E 脚本」已由 Go `exchange_test`/`usage_e2e_test` 覆盖，本计划不重复。
- `proxy` 不进 DEFAULT，避免无 Go 机器默认 start 失败。
