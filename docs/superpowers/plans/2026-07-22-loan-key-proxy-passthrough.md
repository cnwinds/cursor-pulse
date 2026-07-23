# 借用 Key Proxy 透传 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让活跃借用 Cursor Key（`cr*`）经 Go 代理透传认证并上报用量；借用页可一键复制代理命令，并展示「账号近似消耗 + 其中 proxy 精确子集」。

**Architecture:** Pulse authorize 扩展识别借用 `key_hash` → `mode=loan_passthrough`；Go exchange/MITM 用客户端 Key 真换 JWT、不入池；`ProxyKeyUsage` 支持 `loan_id` 归属；管理端 `GET /loans/{id}/client-setup` + `LoansView` UI。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy / Vue 3 + Element Plus / Go 1.22+（`proxy/`）/ pytest / `go test`

**Spec:** `docs/superpowers/specs/2026-07-22-loan-key-proxy-passthrough-design.md`

## Global Constraints

- 透传仅匹配活跃 `KeyLoan` + 凭证 `status=active`；未知 `cr*` → 401。
- 用量主数字 = 账号快照近似差值；副行 = `sum(ProxyKeyUsage.cost_cents where loan_id=…)`，不相加；副行始终显示（含 `$0.00`）。
- 命令模板：`HTTPS_PROXY` + `CURSOR_API_KEY` + `agent -k`；URL 来自 `PROXY_PUBLIC_URL` / `config.proxy.public_url`。
- Cursor Key 前缀判定：`plaintext.startswith("cr")`（覆盖 `crsr_`）。
- `pk_` 路径行为与额度逻辑不得回归破坏。
- 本迭代不做钉钉命令下发、不做透传路径的 quota/5h 限额。

## File Map

| 文件 | 职责 |
|---|---|
| `pulse/storage/models.py` | `AiAccountCredential.key_hash`；`ProxyKeyUsage.proxy_key_id` 可空 + `loan_id` |
| `pulse/storage/migrate.py` | 加列；SQLite 重建 `proxy_key_usages` 使 `proxy_key_id` 可空 |
| `pulse/proxy/keys.py` | 复用 `hash_proxy_key`（凭证哈希同一函数） |
| `pulse/ingestion/credentials.py` | 绑定/签发贷款凭证时写 `key_hash` |
| `pulse/proxy/service.py` | authorize 借用分支；`record_usages` 支持 `loan_id`；贷款用量汇总 helper |
| `pulse/web/internal_proxy_api.py` | `UsageItem`/`EventItem` 字段可空扩展 |
| `pulse/tool_center/key_loans.py` | `loan_payload` 增 `proxy_cost_cents` |
| `pulse/web/quota_api.py` | `GET /api/v2/loans/{id}/client-setup` |
| `proxy/session.go` | `SessionBinding` 扩 Mode/LoanID/CredentialID |
| `proxy/pulse_client.go` | `AuthResult`/`UsageItem`/`EventItem` 扩字段 |
| `proxy/mitm.go` | exchange + 业务透传路径 |
| `proxy/*_test.go` | 透传回归 |
| `web-admin/src/views/LoansView.vue` | 复制命令 + 用量副行 |
| `tests/test_web_internal_proxy.py` | authorize/usage 贷款路径 |
| `tests/test_loan_proxy_schema.py` | schema 迁移 |
| `tests/test_loan_client_setup.py` | client-setup + payload |

---

### Task 1: Schema — `key_hash` + `ProxyKeyUsage.loan_id` / nullable `proxy_key_id`

**Files:**
- Modify: `pulse/storage/models.py`
- Modify: `pulse/storage/migrate.py`
- Test: `tests/test_loan_proxy_schema.py`（新建）

**Interfaces:**
- Produces: `AiAccountCredential.key_hash: str | None`；`ProxyKeyUsage.loan_id: str | None`；`ProxyKeyUsage.proxy_key_id: str | None`

- [ ] **Step 1: Write failing test — models accept loan-only usage row**

```python
# tests/test_loan_proxy_schema.py
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from pulse.storage.migrate import migrate_schema
from pulse.storage.models import Base, ProxyKeyUsage


def test_proxy_key_usage_allows_loan_id_without_proxy_key(tmp_path):
    url = f"sqlite:///{tmp_path / 't.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    migrate_schema(engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("proxy_key_usages")}
    assert "loan_id" in cols
    assert cols["proxy_key_id"]["nullable"] is True
    cred_cols = {c["name"] for c in inspect(engine).get_columns("ai_account_credentials")}
    assert "key_hash" in cred_cols

    with Session(engine) as s:
        s.add(
            ProxyKeyUsage(
                proxy_key_id=None,
                loan_id="loan-1",
                credential_id="cred-1",
                total_tokens=10,
                cost_cents=3,
            )
        )
        s.commit()
```

- [ ] **Step 2: Run test — expect FAIL (missing columns / NOT NULL)**

```powershell
pytest tests/test_loan_proxy_schema.py -v
```

Expected: FAIL（缺 `loan_id` / `key_hash` 或 `proxy_key_id` 不可空）

- [ ] **Step 3: Update models**

在 `AiAccountCredential` 增加：

```python
key_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
```

将 `ProxyKeyUsage` 改为：

```python
proxy_key_id: Mapped[str | None] = mapped_column(
    ForeignKey("proxy_keys.id"), nullable=True, index=True
)
loan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
```

- [ ] **Step 4: Update migrate**

- `_CREDENTIAL_PROXY_COLUMNS` 增加 `"key_hash": "VARCHAR(64)"`。
- `_PROXY_USAGE_COLUMNS` 增加 `"loan_id": "VARCHAR(36)"`。
- 新增 `_sqlite_rebuild_proxy_key_usages_nullable_proxy_key(engine)`：若 `proxy_key_usages.proxy_key_id` 仍为 NOT NULL，则按现有 `_sqlite_rebuild_*` 风格重建表（`proxy_key_id VARCHAR(36) NULL`，保留 `loan_id`）。在 `migrate_schema` 中、`create_all` 前调用。
- 新鲜库：`create_all` 直接用新模型即可。

- [ ] **Step 5: Run test — expect PASS**

```powershell
pytest tests/test_loan_proxy_schema.py -v
```

- [ ] **Step 6: Commit**

```powershell
git add pulse/storage/models.py pulse/storage/migrate.py tests/test_loan_proxy_schema.py
git commit -m "feat(storage): allow loan-attributed proxy usage and credential key_hash"
```

---

### Task 2: 凭证写 `key_hash` + authorize 借用透传

**Files:**
- Modify: `pulse/ingestion/credentials.py`（`bind`/`create_loan_credential` 写 hash）
- Modify: `pulse/proxy/service.py`（`authorize_status`）
- Test: `tests/test_web_internal_proxy.py`（追加用例）

**Interfaces:**
- Consumes: `hash_proxy_key(plaintext) -> str` from `pulse.proxy.keys`
- Produces: `authorize_status` 对 `cr*` 返回  
  `{status, mode: "loan_passthrough", proxy_key_id: None, loan_id, credential_id, reason}`

- [ ] **Step 1: Write failing tests**

在 `tests/test_web_internal_proxy.py` 追加（沿用 fixture，创建 `KeyLoan` + loan 角色凭证，`key_hash=hash_proxy_key(plaintext)`）：

```python
def test_authorize_loan_passthrough_ok(env):
    # plaintext = "crsr_test_loan_key_abc"
    # POST /api/internal/v1/proxy/authorize {"pulse_key": plaintext}
    # assert status=="ok", mode=="loan_passthrough", loan_id, credential_id, proxy_key_id is None

def test_authorize_loan_revoked_invalid(env):
    # loan.status=revoked → status invalid

def test_authorize_unknown_cr_invalid(env):
    # pulse_key="crsr_unknown" → invalid
```

同时跑现有 `pk_` authorize 用例。

- [ ] **Step 2: Run — expect FAIL**

```powershell
pytest tests/test_web_internal_proxy.py::test_authorize_loan_passthrough_ok -v
```

- [ ] **Step 3: Write `key_hash` on credential create**

`pulse/ingestion/credentials.py`：

```python
from pulse.proxy.keys import hash_proxy_key
# bind primary + create_loan_credential:
cred.key_hash = hash_proxy_key(api_key)  # 新建与更新路径都写
```

- [ ] **Step 4: Extend `authorize_status`**

```python
def authorize_status(session, plaintext, *, now=None) -> dict:
    plaintext = (plaintext or "").strip()
    if plaintext.startswith("pk_"):
        # 现有逻辑（可抽 _authorize_proxy_key）
        ...
    if plaintext.startswith("cr"):
        return _authorize_loan_passthrough(session, plaintext)
    return {"status": "invalid", "proxy_key_id": None, "mode": None, "reason": "unknown_key"}

def _authorize_loan_passthrough(session, plaintext: str) -> dict:
    from pulse.storage.models import AiAccountCredential, KeyLoan
    h = hash_proxy_key(plaintext)
    cred = session.scalar(
        select(AiAccountCredential).where(
            AiAccountCredential.key_hash == h,
            AiAccountCredential.status == "active",
            AiAccountCredential.key_role == "loan",
        )
    )
    if cred is None:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": None,
            "credential_id": None,
            "reason": "unknown_key",
        }
    loan = session.scalar(
        select(KeyLoan).where(
            KeyLoan.credential_id == cred.id,
            KeyLoan.status == "active",
        )
    )
    if loan is None:
        return {
            "status": "invalid",
            "proxy_key_id": None,
            "mode": None,
            "loan_id": None,
            "credential_id": cred.id,
            "reason": "loan_inactive",
        }
    return {
        "status": "ok",
        "mode": "loan_passthrough",
        "proxy_key_id": None,
        "loan_id": loan.id,
        "credential_id": cred.id,
        "reason": None,
    }
```

现有 `pk_` 成功响应可显式 `loan_id: None` 以保持字段稳定。

- [ ] **Step 5: Run tests — PASS**

```powershell
pytest tests/test_web_internal_proxy.py -v -k "authorize"
```

- [ ] **Step 6: Commit**

```powershell
git add pulse/ingestion/credentials.py pulse/proxy/service.py tests/test_web_internal_proxy.py
git commit -m "feat(proxy): authorize active loan cursor keys as passthrough"
```

---

### Task 3: `record_usages` 支持 `loan_id` + 内部 API 字段

**Files:**
- Modify: `pulse/proxy/service.py` — `record_usages`；`record_event` 支持 `loan_id`
- Modify: `pulse/web/internal_proxy_api.py` — `UsageItem`/`EventItem`
- Modify: `pulse/storage/models.py` — `ProxyEvent.loan_id` 可空
- Modify: `pulse/storage/migrate.py` — 事件表加列
- Test: `tests/test_web_internal_proxy.py`

**Interfaces:**
- Consumes: usage item dict with optional `proxy_key_id` / `loan_id`
- Produces: rows with exactly one of the two set；loan 路径不做 `evaluate_key` 停用

- [ ] **Step 1: Failing test**

```python
def test_record_usage_by_loan_id(env):
    # POST /api/internal/v1/proxy/usage
    # items: [{loan_id, credential_id, model, tokens:{input:1,...}}]
    # assert recorded==1; DB row proxy_key_id is None, loan_id set, cost_cents>=0

def test_record_usage_missing_both_ids_skipped(env):
    # items: [{tokens:{input:1}}] → recorded==0
```

- [ ] **Step 2: Run — FAIL**

- [ ] **Step 3: Implement**

`UsageItem`:

```python
class UsageItem(BaseModel):
    proxy_key_id: str | None = None
    loan_id: str | None = None
    credential_id: str | None = None
    model: str | None = None
    tokens: dict[str, int] = {}
    ts: datetime | None = None
    request_id: str | None = None
```

`EventItem` 增加 `loan_id: str | None = None`。

`ProxyEvent` 增加：

```python
loan_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
```

`record_usages` 核心分支：

```python
proxy_key_id = item.get("proxy_key_id") or None
loan_id = item.get("loan_id") or None
if bool(proxy_key_id) == bool(loan_id):
    continue  # 都有或都无 → skip
if proxy_key_id:
    # 现有逻辑
    ...
else:
    loan = session.get(KeyLoan, loan_id)
    if loan is None:
        continue
    # dedupe by (loan_id, request_id) if request_id
    # pricing via loan.source_account_id → AiAccount.team_id
    session.add(ProxyKeyUsage(proxy_key_id=None, loan_id=loan.id, ...))
    # 不加入 touched / evaluate_key
```

- [ ] **Step 4: Tests PASS**

```powershell
pytest tests/test_web_internal_proxy.py -v -k "usage or authorize"
```

- [ ] **Step 5: Commit**

```powershell
git add pulse/proxy/service.py pulse/web/internal_proxy_api.py pulse/storage/models.py pulse/storage/migrate.py tests/test_web_internal_proxy.py
git commit -m "feat(proxy): record usage and events attributed to loan_id"
```

---

### Task 4: `loan_payload` 汇总 + `client-setup` API

**Files:**
- Modify: `pulse/tool_center/key_loans.py` — `loan_payload`
- Modify: `pulse/web/quota_api.py` — client-setup 路由
- Modify: `pulse/proxy/service.py` — `loan_proxy_totals`；复用 `build_client_command`
- Test: `tests/test_loan_client_setup.py`（新建）

**Interfaces:**
- Produces:
  - `loan_payload[...]["proxy_cost_cents"]: int`
  - `GET /api/v2/loans/{id}/client-setup?shell=` → `{plaintext_key, proxy_url, shell, command}`

- [ ] **Step 1: Failing tests**

```python
def test_loan_payload_includes_proxy_cost(client_with_loan_and_usage):
    # list loans → item.proxy_cost_cents == sum

def test_loan_client_setup_powershell(client_auth):
    # GET .../client-setup?shell=powershell
    # assert "HTTPS_PROXY" in command and "CURSOR_API_KEY" and "agent -k"

def test_loan_client_setup_revoked_410(client_auth):
    ...
```

鉴权：与贷款签发相同（`accounts:write`）。`proxy_url` 从 `config.proxy.public_url`（空则默认 `http://127.0.0.1:8317`，与 `pulse/web/proxy_keys_api.py` 一致）。

- [ ] **Step 2: Implement helpers + route**

```python
def loan_proxy_totals(session, loan_id: str) -> tuple[int, int]:
    row = session.execute(
        select(
            func.coalesce(func.sum(ProxyKeyUsage.total_tokens), 0),
            func.coalesce(func.sum(ProxyKeyUsage.cost_cents), 0),
        ).where(ProxyKeyUsage.loan_id == loan_id)
    ).one()
    return int(row[0]), int(row[1])
```

`loan_payload` 增加 `"proxy_cost_cents": loan_proxy_totals(...)[1]`。

client-setup：解密 `loan.credential_id` → `build_client_command`；非 active → 410。

- [ ] **Step 3: Tests PASS + Commit**

```powershell
pytest tests/test_loan_client_setup.py -v
git add pulse/tool_center/key_loans.py pulse/web/quota_api.py pulse/proxy/service.py tests/test_loan_client_setup.py
git commit -m "feat(loans): client-setup command and proxy_cost_cents on payload"
```

---

### Task 5: Go — AuthResult / Session / Usage 字段 + 透传 exchange

**Files:**
- Modify: `proxy/pulse_client.go`
- Modify: `proxy/session.go`
- Modify: `proxy/mitm.go`
- Test: `proxy/exchange_test.go` 或新建 `proxy/loan_passthrough_test.go`

**Interfaces:**
- Consumes: authorize JSON with `mode=loan_passthrough`, `loan_id`, `credential_id`
- Produces: session bound with Mode/LoanID/CredentialID/PulseKey；usage items with `loan_id`

- [ ] **Step 1: Extend structs**

```go
type AuthResult struct {
	Status       string  `json:"status"`
	ProxyKeyID   string  `json:"proxy_key_id"`
	Mode         string  `json:"mode"`
	LoanID       string  `json:"loan_id"`
	CredentialID string  `json:"credential_id"`
	Reason       *string `json:"reason"`
}

type SessionBinding struct {
	ProxyKeyID   string
	PulseKey     string
	Mode         string
	LoanID       string
	CredentialID string
}

type UsageItem struct {
	ProxyKeyID   string      `json:"proxy_key_id,omitempty"`
	LoanID       string      `json:"loan_id,omitempty"`
	CredentialID string      `json:"credential_id,omitempty"`
	Model        string      `json:"model,omitempty"`
	Tokens       TokenCounts `json:"tokens"`
	TS           string      `json:"ts,omitempty"`
	RequestID    string      `json:"request_id,omitempty"`
}

type EventItem struct {
	EventType    string `json:"event_type"`
	ProxyKeyID   string `json:"proxy_key_id,omitempty"`
	LoanID       string `json:"loan_id,omitempty"`
	CredentialID string `json:"credential_id,omitempty"`
	Detail       string `json:"detail,omitempty"`
}
```

JSON `null` 解成 Go 空字符串；分支用 `res.Mode == "loan_passthrough"`。

- [ ] **Step 2: Failing test — passthrough exchange does not touch pool**

假 Pulse authorize 返回 `loan_passthrough`；假 Cursor exchange 校验 `Authorization: Bearer crsr_...`；池为空也应成功。

- [ ] **Step 3: Implement `handleExchange` branch**

```go
if res.Mode == "loan_passthrough" {
    // mint JWT with client's pulseKey (reuse ensureToken-style HTTP)
    // Bind(token, SessionBinding{Mode, LoanID, CredentialID, PulseKey: pulseKey})
    // return accessToken
    return
}
// existing pool path
```

抽 `exchangeCursorAPIKey(ctx, apiKey) (jwt string, err error)`。

- [ ] **Step 4: `go test` PASS + Commit**

```powershell
cd proxy; go test ./... -count=1 -v -run Exchange
git add proxy/*.go
git commit -m "feat(proxy-go): loan passthrough exchange without pool"
```

---

### Task 6: Go — 业务 MITM 透传 + usage/events 带 `loan_id`

**Files:**
- Modify: `proxy/mitm.go`
- Modify: `proxy/server.go`（透传 entry 缓存若需要）
- Test: `proxy/loan_passthrough_test.go`

**Interfaces:**
- Consumes: `SessionBinding.Mode == "loan_passthrough"`
- Produces: upstream Auth = JWT from loan key only；usage `LoanID` set, `ProxyKeyID` empty

- [ ] **Step 1: Failing test**

先 exchange 透传拿到 JWT，再打业务路径；上游 Bearer 为透传 JWT；usage 含 `loan_id`。

- [ ] **Step 2: Implement MITM branch**

```go
if binding.Mode == "loan_passthrough" {
    entry, token, err := s.passthroughToken(req.Context(), binding)
    // single attempt; auth fail → event with LoanID; no pool rotate
    // usage tap: LoanID=binding.LoanID, CredentialID=binding.CredentialID, ProxyKeyID=""
} else {
    // existing pool loop
}
```

`passthroughToken`：进程内 `map[credentialID]*keyEntry`，`apiKey=binding.PulseKey`，`ensureToken`。

- [ ] **Step 3: `go test ./... -count=1` PASS + Commit**

```powershell
cd proxy; go test ./... -count=1
git add proxy/*.go
git commit -m "feat(proxy-go): passthrough MITM and loan-attributed usage"
```

---

### Task 7: 前端 `LoansView` — 复制命令 + 用量副行

**Files:**
- Modify: `web-admin/src/views/LoansView.vue`
- 参考: `web-admin/src/views/ProxyKeysView.vue` 复制命令下拉

**Interfaces:**
- Consumes: `proxy_cost_cents`；`GET /api/v2/loans/{id}/client-setup`

- [ ] **Step 1: UI 改动**

1. 页头 desc：补充「走 proxy 的部分另有精确子计数」。
2. 「近似消耗」列：主行 `$X.XX`；副行始终 `其中 proxy $Y.YY`（`proxy_cost_cents ?? 0`）。
3. 操作列加宽；active 行 `el-dropdown`「复制命令」（PowerShell / Linux），调 client-setup。
4. 分配成功弹窗：保留复制 Key；增加复制命令（优先用 `revealedKey.loan_id` 调 client-setup）；更新 alert 文案。

- [ ] **Step 2: 无独立前端单测则跳过自动化；目视/手工点一次**

- [ ] **Step 3: Commit**

```powershell
git add web-admin/src/views/LoansView.vue
git commit -m "feat(web-admin): loan proxy copy-command and proxy cost subtitle"
```

---

### Task 8: 回归与收尾

- [ ] **Step 1: 全量相关测试**

```powershell
pytest tests/test_loan_proxy_schema.py tests/test_web_internal_proxy.py tests/test_loan_client_setup.py tests/test_web_proxy_admin.py -v
cd proxy; go test ./... -count=1
```

Expected: 全绿。

- [ ] **Step 2: 旧借用无 `key_hash`**

不做 migrate 内解密回填（启动 migrate 无加密 key）。新签发/绑定会写 hash；旧活跃借用需重新签发才能透传——与 spec 一致。

- [ ] **Step 3: 若有修复则再 commit**

---

## Spec coverage checklist

| Spec 项 | Task |
|---|---|
| `key_hash` 列 + 签发写入 | 1, 2 |
| `ProxyKeyUsage` loan_id / nullable proxy_key_id | 1, 3 |
| authorize `loan_passthrough` | 2 |
| usage/events 挂 loan | 3, 6 |
| Go 透传 exchange + MITM | 5, 6 |
| client-setup + 命令模板 | 4, 7 |
| 借用页复制命令 + 副行用量 | 7 |
| 撤销即 invalid / 410 | 2, 4 |
| `pk_` 回归 | 2, 5, 8 |
| 非目标（钉钉/相加/限额） | 不实现 |
