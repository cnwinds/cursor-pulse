# Cursor 代理 · Go 数据面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 comate-cursor-proxy 快照引入本仓库 `proxy/`，改造为 Pulse 控制面驱动的数据面：脉冲 key 授权、cursor 凭证池热更新、exchange 拦截与 JWT→归属映射、TurnEnded 用量旁路上报、换号/耗尽事件上报。

**Architecture:** 独立 Go 进程（零第三方依赖）监听 `HTTPS_PROXY` CONNECT MITM（仅 `*.cursor.sh`）。控制面契约已由计划 1 交付：`/api/internal/v1/proxy/{authorize,pool,usage,events}`（`PULSE_INTERNAL_SERVICE_TOKEN`）。本计划不改 Pulse Python 代码；部署挂载（`cursor-pulse.bat`）留计划 3。

**Tech Stack:** Go 1.22+（stdlib only）/ `go test` / 假上游 httptest + 假 Pulse httptest。

**Spec:** `docs/superpowers/specs/2026-07-22-cursor-proxy-integration-design.md` §5–6、§9–10

**基线来源（快照拷贝，用户已确认）：** `<legacy-proxy-snapshot-path>`  
**用量旁路参考（只读移植算法，不拷贝整个 Node 项目）：** `D:\projects\cursor-proxy\src\proto.js`

**约定：**
- 模块路径保持 `module cursor-proxy`，源码目录 `proxy/`（`package main`）。
- 运行测试：`cd proxy && go test ./... -count=1 -v`（仓库根目录外进入 `proxy/`）。
- 先确认本机已安装 Go：`go version`（若无，安装 1.22+ 并加入 PATH 后再执行任何 Task）。
- 每个 Task 结束后按步骤提交 git。
- Pulse 不可达：新 authorize fail-closed；授权缓存 TTL 60s 内放行；usage 缓冲重试有上限后丢弃+日志。
- `credential_id` 贯穿池条目与 usage/events；热更新按 `credential_id` 合并，保留同 id 的 exhausted/jwt 冷却状态。

**文件地图（改造后）：**

| 文件 | 职责 |
|---|---|
| `proxy/ca.go` `connect.go` `frames.go` `server.go` | 基线原样保留（小改仅必要时） |
| `proxy/config.go` `main.go` | Pulse URL/token/listen；不再强制 `-keys` |
| `proxy/pool.go` | 池条目带 `credentialID`；`ReplaceFromPulse` 热更新 |
| `proxy/pulse_client.go` | authorize / pool / usage / events HTTP 客户端 + 缓存/批量 |
| `proxy/session.go` | JWT → `{proxyKeyID, pulseKey}` 映射 |
| `proxy/mitm.go` | 拦截 `/auth/exchange_user_api_key`；业务路径查映射；接 usage tap |
| `proxy/usage_tap.go` | TurnEndedUpdate 旁路解析（移植 Node `findTurnEnded`） |
| `proxy/*_test.go` | 基线测试保留 + Pulse/exchange/usage 新测 |

---

### Task 1: 快照引入基线并确认可编译测试

**Files:**
- Create: `proxy/` 下全部基线 `.go` / `go.mod` / `README.md` / `cursor-agent-proxy.ps1`（**不要**拷贝 `*.exe`）
- Test: 沿用 `proxy/connect_test.go` `proxy/proxy_test.go`

- [x] **Step 1: 确认 Go 可用**

Run:

```powershell
go version
```

Expected: `go version go1.22` 或更高。若命令不存在：安装 Go 1.22+，重新开终端后再继续（**不要跳过**）。

- [x] **Step 2: 拷贝基线源码**

Run（PowerShell，仓库根目录）：

```powershell
New-Item -ItemType Directory -Force -Path proxy | Out-Null
$src = "<legacy-proxy-snapshot-path>"
$files = @(
  "ca.go","config.go","connect.go","connect_test.go","frames.go",
  "main.go","mitm.go","pool.go","proxy_test.go","server.go",
  "go.mod","README.md","cursor-agent-proxy.ps1"
)
foreach ($f in $files) {
  Copy-Item -Force (Join-Path $src $f) (Join-Path "proxy" $f)
}
# 将 go.mod 的 go 版本钉到 1.22（兼容面更广；若本机更高可保留）
(Get-Content proxy\go.mod) -replace '^go .+$','go 1.22' | Set-Content proxy\go.mod
```

Expected: `proxy/` 含上述文件，无 `.exe`。

- [x] **Step 3: 跑基线测试**

Run:

```powershell
cd proxy
go test ./... -count=1
```

Expected: PASS（基线 `connect_test` + `proxy_test` 端到端轮换）。

- [x] **Step 4: 提交**

```bash
git add proxy/
git commit -m "chore(proxy): vendor comate-cursor-proxy baseline snapshot"
```

---

### Task 2: Pulse 客户端（`pulse_client.go`）

**Files:**
- Create: `proxy/pulse_client.go`
- Create: `proxy/pulse_client_test.go`

- [x] **Step 1: 写失败测试**

创建 `proxy/pulse_client_test.go`：

```go
package main

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

func TestPulseClientAuthorizeAndCache(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			t.Fatalf("path %s", r.URL.Path)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer tok" {
			t.Fatalf("auth %q", got)
		}
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "proxy_key_id": "pk1", "mode": "quota", "reason": nil,
		})
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", 50*time.Millisecond)
	a1, err := c.Authorize("pk_abc")
	if err != nil || a1.Status != "ok" || a1.ProxyKeyID != "pk1" {
		t.Fatalf("a1=%+v err=%v", a1, err)
	}
	a2, err := c.Authorize("pk_abc")
	if err != nil || hits.Load() != 1 {
		t.Fatalf("cache miss: hits=%d err=%v a2=%+v", hits.Load(), err, a2)
	}
	time.Sleep(60 * time.Millisecond)
	_, err = c.Authorize("pk_abc")
	if err != nil || hits.Load() != 2 {
		t.Fatalf("ttl refresh: hits=%d err=%v", hits.Load(), err)
	}
}

func TestPulseClientAuthorizeFailClosed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()
	c := NewPulseClient(srv.URL, "tok", time.Minute)
	_, err := c.Authorize("pk_x")
	if err == nil {
		t.Fatal("expected error")
	}
}

func TestPulseClientFetchPool(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"credentials": []map[string]string{
				{"credential_id": "c1", "api_key": "key1"},
			},
		})
	}))
	defer srv.Close()
	c := NewPulseClient(srv.URL, "tok", time.Minute)
	creds, err := c.FetchPool()
	if err != nil || len(creds) != 1 || creds[0].CredentialID != "c1" {
		t.Fatalf("%+v %v", creds, err)
	}
}

func TestPulseClientUsageBatchFlush(t *testing.T) {
	var bodies [][]byte
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		b, _ := io.ReadAll(r.Body)
		bodies = append(bodies, b)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
	}))
	defer srv.Close()
	c := NewPulseClient(srv.URL, "tok", time.Minute)
	c.usageFlushEvery = 30 * time.Millisecond
	c.usageBatchMax = 2
	c.Start()
	defer c.Stop()
	c.EnqueueUsage(UsageItem{ProxyKeyID: "pk1", CredentialID: "c1", Model: "m", Tokens: TokenCounts{Input: 1}})
	c.EnqueueUsage(UsageItem{ProxyKeyID: "pk1", CredentialID: "c1", Model: "m", Tokens: TokenCounts{Input: 2}})
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if len(bodies) >= 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if len(bodies) < 1 {
		t.Fatal("no flush")
	}
}
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd proxy && go test -run TestPulseClient -count=1`
Expected: FAIL（`NewPulseClient` undefined）

- [x] **Step 3: 实现 `pulse_client.go`**

创建 `proxy/pulse_client.go`：

```go
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"sync"
	"time"
)

type AuthResult struct {
	Status     string  `json:"status"`
	ProxyKeyID string  `json:"proxy_key_id"`
	Mode       string  `json:"mode"`
	Reason     *string `json:"reason"`
}

type PoolCredential struct {
	CredentialID string `json:"credential_id"`
	APIKey       string `json:"api_key"`
}

type TokenCounts struct {
	Input      int64 `json:"input"`
	Output     int64 `json:"output"`
	CacheRead  int64 `json:"cache_read"`
	CacheWrite int64 `json:"cache_write"`
	Reasoning  int64 `json:"reasoning"`
}

type UsageItem struct {
	ProxyKeyID   string      `json:"proxy_key_id"`
	CredentialID string      `json:"credential_id,omitempty"`
	Model        string      `json:"model,omitempty"`
	Tokens       TokenCounts `json:"tokens"`
	TS           string      `json:"ts,omitempty"`
	RequestID    string      `json:"request_id,omitempty"`
}

type EventItem struct {
	EventType    string `json:"event_type"`
	ProxyKeyID   string `json:"proxy_key_id,omitempty"`
	CredentialID string `json:"credential_id,omitempty"`
	Detail       string `json:"detail,omitempty"`
}

type PulseClient struct {
	baseURL string
	token   string
	client  *http.Client

	authTTL time.Duration
	authMu  sync.Mutex
	authCache map[string]struct {
		res    AuthResult
		expiry time.Time
	}

	usageBatchMax   int
	usageFlushEvery time.Duration
	usageMaxRetries int
	usageMu         sync.Mutex
	usageBuf        []UsageItem
	stopCh          chan struct{}
	wg              sync.WaitGroup
}

func NewPulseClient(baseURL, token string, authTTL time.Duration) *PulseClient {
	if authTTL <= 0 {
		authTTL = 60 * time.Second
	}
	return &PulseClient{
		baseURL:         stringsTrimRightSlash(baseURL),
		token:           token,
		client:          &http.Client{Timeout: 15 * time.Second},
		authTTL:         authTTL,
		authCache:       map[string]struct {
			res    AuthResult
			expiry time.Time
		}{},
		usageBatchMax:   50,
		usageFlushEvery: 5 * time.Second,
		usageMaxRetries: 3,
		stopCh:          make(chan struct{}),
	}
}

func stringsTrimRightSlash(s string) string {
	for len(s) > 0 && s[len(s)-1] == '/' {
		s = s[:len(s)-1]
	}
	return s
}

func (c *PulseClient) Start() {
	c.wg.Add(1)
	go func() {
		defer c.wg.Done()
		t := time.NewTicker(c.usageFlushEvery)
		defer t.Stop()
		for {
			select {
			case <-c.stopCh:
				c.flushUsage(true)
				return
			case <-t.C:
				c.flushUsage(false)
			}
		}
	}()
}

func (c *PulseClient) Stop() {
	select {
	case <-c.stopCh:
	default:
		close(c.stopCh)
	}
	c.wg.Wait()
}

func (c *PulseClient) Authorize(pulseKey string) (AuthResult, error) {
	c.authMu.Lock()
	if e, ok := c.authCache[pulseKey]; ok && time.Now().Before(e.expiry) {
		res := e.res
		c.authMu.Unlock()
		return res, nil
	}
	c.authMu.Unlock()

	body, _ := json.Marshal(map[string]string{"pulse_key": pulseKey})
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/authorize", bytes.NewReader(body))
	if err != nil {
		return AuthResult{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return AuthResult{}, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode != http.StatusOK {
		return AuthResult{}, fmt.Errorf("authorize HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var res AuthResult
	if err := json.Unmarshal(raw, &res); err != nil {
		return AuthResult{}, err
	}
	c.authMu.Lock()
	c.authCache[pulseKey] = struct {
		res    AuthResult
		expiry time.Time
	}{res: res, expiry: time.Now().Add(c.authTTL)}
	c.authMu.Unlock()
	return res, nil
}

func (c *PulseClient) FetchPool() ([]PoolCredential, error) {
	req, err := http.NewRequest(http.MethodGet, c.baseURL+"/api/internal/v1/proxy/pool", nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("pool HTTP %d: %s", resp.StatusCode, truncate(string(raw), 200))
	}
	var out struct {
		Credentials []PoolCredential `json:"credentials"`
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, err
	}
	return out.Credentials, nil
}

func (c *PulseClient) EnqueueUsage(item UsageItem) {
	if item.TS == "" {
		item.TS = time.Now().UTC().Format(time.RFC3339)
	}
	c.usageMu.Lock()
	c.usageBuf = append(c.usageBuf, item)
	flushNow := len(c.usageBuf) >= c.usageBatchMax
	c.usageMu.Unlock()
	if flushNow {
		c.flushUsage(false)
	}
}

func (c *PulseClient) ReportEvent(ev EventItem) {
	payload, _ := json.Marshal(map[string]any{"events": []EventItem{ev}})
	req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/events", bytes.NewReader(payload))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.token)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	req = req.WithContext(ctx)
	resp, err := c.client.Do(req)
	if err != nil {
		log.Printf("[pulse] report event: %v", err)
		return
	}
	resp.Body.Close()
}

func (c *PulseClient) flushUsage(force bool) {
	c.usageMu.Lock()
	if len(c.usageBuf) == 0 {
		c.usageMu.Unlock()
		return
	}
	batch := append([]UsageItem(nil), c.usageBuf...)
	c.usageBuf = c.usageBuf[:0]
	c.usageMu.Unlock()

	payload, _ := json.Marshal(map[string]any{"items": batch})
	var lastErr error
	for attempt := 0; attempt < c.usageMaxRetries; attempt++ {
		req, err := http.NewRequest(http.MethodPost, c.baseURL+"/api/internal/v1/proxy/usage", bytes.NewReader(payload))
		if err != nil {
			lastErr = err
			break
		}
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer "+c.token)
		resp, err := c.client.Do(req)
		if err != nil {
			lastErr = err
			time.Sleep(time.Duration(attempt+1) * 200 * time.Millisecond)
			continue
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			return
		}
		lastErr = fmt.Errorf("usage HTTP %d", resp.StatusCode)
		time.Sleep(time.Duration(attempt+1) * 200 * time.Millisecond)
	}
	log.Printf("[pulse] usage flush dropped %d items after retries: %v (force=%v)", len(batch), lastErr, force)
}
```

（若编译器抱怨未使用 `force` 以外变量，保持如上即可。）

- [x] **Step 4: 跑测试确认通过**

Run: `cd proxy && go test -run TestPulseClient -count=1`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add proxy/pulse_client.go proxy/pulse_client_test.go
git commit -m "feat(proxy): Pulse control-plane HTTP client with auth cache and usage batching"
```

---

### Task 3: 会话映射 + 池热更新（`session.go` / `pool.go`）

**Files:**
- Create: `proxy/session.go`
- Create: `proxy/session_test.go`
- Modify: `proxy/pool.go`
- Create: `proxy/pool_pulse_test.go`

- [x] **Step 1: 写会话映射测试**

创建 `proxy/session_test.go`：

```go
package main

import "testing"

func TestSessionMapBindAndLookup(t *testing.T) {
	m := NewSessionMap()
	m.Bind("jwt1", SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_abc"})
	b, ok := m.Lookup("jwt1")
	if !ok || b.ProxyKeyID != "pk1" {
		t.Fatalf("%+v %v", b, ok)
	}
	if _, ok := m.Lookup("missing"); ok {
		t.Fatal("expected miss")
	}
}
```

- [x] **Step 2: 实现 `session.go`**

```go
package main

import "sync"

type SessionBinding struct {
	ProxyKeyID string
	PulseKey   string
}

type SessionMap struct {
	mu   sync.RWMutex
	byJWT map[string]SessionBinding
}

func NewSessionMap() *SessionMap {
	return &SessionMap{byJWT: map[string]SessionBinding{}}
}

func (m *SessionMap) Bind(jwt string, b SessionBinding) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.byJWT[jwt] = b
}

func (m *SessionMap) Lookup(jwt string) (SessionBinding, bool) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	b, ok := m.byJWT[jwt]
	return b, ok
}
```

- [x] **Step 3: 改 `pool.go` — keyEntry 带 credentialID + ReplaceFromPulse**

在 `type keyEntry struct` 顶部增加字段：

```go
	credentialID string
```

将 `NewPool` 改为同时接受可选 id（保持旧签名兼容测试）：

```go
func NewPool(keys []string) *Pool {
	creds := make([]PoolCredential, 0, len(keys))
	for i, k := range keys {
		creds = append(creds, PoolCredential{CredentialID: fmt.Sprintf("local-%d", i), APIKey: k})
	}
	return NewPoolFromCredentials(creds)
}

func NewPoolFromCredentials(creds []PoolCredential) *Pool {
	p := &Pool{
		client:       &http.Client{Timeout: 20 * time.Second},
		exchangeBase: "https://api2.cursor.sh",
	}
	for _, c := range creds {
		p.keys = append(p.keys, &keyEntry{credentialID: c.CredentialID, apiKey: c.APIKey})
	}
	return p
}
```

在 `keyEntry` 上增加：

```go
func (e *keyEntry) id() string {
	if e.credentialID != "" {
		return e.credentialID
	}
	return e.masked()
}
```

追加热更新（按 credential_id 合并，保留 exhausted/jwt/exp）：

```go
// ReplaceFromPulse merges Pulse pool credentials into the live pool.
// Same credential_id keeps exhaustion + cached JWT; removed ids are dropped;
// new ids are appended. Cursor position is clamped.
func (p *Pool) ReplaceFromPulse(creds []PoolCredential) {
	p.mu.Lock()
	defer p.mu.Unlock()
	byID := map[string]*keyEntry{}
	for _, e := range p.keys {
		byID[e.credentialID] = e
	}
	var next []*keyEntry
	seen := map[string]bool{}
	for _, c := range creds {
		if c.CredentialID == "" || c.APIKey == "" {
			continue
		}
		seen[c.CredentialID] = true
		if old, ok := byID[c.CredentialID]; ok {
			old.apiKey = c.APIKey
			next = append(next, old)
			continue
		}
		next = append(next, &keyEntry{credentialID: c.CredentialID, apiKey: c.APIKey})
	}
	p.keys = next
	if p.cur >= len(p.keys) {
		p.cur = 0
	}
	log.Printf("[pool] hot-updated: %d credential(s)", len(p.keys))
}
```

- [x] **Step 4: 热更新测试**

创建 `proxy/pool_pulse_test.go`：

```go
package main

import "testing"

func TestPoolReplaceFromPulseKeepsExhaustion(t *testing.T) {
	p := NewPoolFromCredentials([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1"},
		{CredentialID: "c2", APIKey: "k2"},
	})
	p.keys[0].exhausted = true
	p.keys[0].jwt = "oldjwt"
	p.ReplaceFromPulse([]PoolCredential{
		{CredentialID: "c1", APIKey: "k1-new"},
		{CredentialID: "c3", APIKey: "k3"},
	})
	if len(p.keys) != 2 {
		t.Fatalf("len=%d", len(p.keys))
	}
	if p.keys[0].credentialID != "c1" || !p.keys[0].exhausted || p.keys[0].jwt != "oldjwt" || p.keys[0].apiKey != "k1-new" {
		t.Fatalf("c1 not preserved: %+v", *p.keys[0])
	}
	if p.keys[1].credentialID != "c3" {
		t.Fatalf("expected c3, got %s", p.keys[1].credentialID)
	}
}
```

- [x] **Step 5: 跑测试**

Run: `cd proxy && go test -run "TestSessionMap|TestPoolReplace|TestProxy|TestConnect" -count=1`
Expected: PASS（含既有基线测）

- [x] **Step 6: 提交**

```bash
git add proxy/session.go proxy/session_test.go proxy/pool.go proxy/pool_pulse_test.go
git commit -m "feat(proxy): session JWT map and Pulse pool hot-update merge"
```

---

### Task 4: exchange 拦截（改造 `mitm.go` + `server.go` + `main.go` + `config.go`）

**Files:**
- Modify: `proxy/config.go` `proxy/main.go` `proxy/server.go` `proxy/mitm.go`
- Create: `proxy/exchange_test.go`

**行为（spec §5 / §6）：**

1. `POST /auth/exchange_user_api_key` + `Authorization: Bearer <pulse_key>`：
   - 调 `Authorize`；`invalid`→401；`suspended`→403+reason；`window_limited`→返回 Connect `resource_exhausted` JSON（agent 稍后重试）；`ok`→池换真 JWT，`sessions.Bind`，按上游格式 `{"accessToken":"...","refreshToken":"..."}` 返回
2. 非 `/auth/*`：从 `Authorization` 取 JWT → `sessions.Lookup`；丢失→401；命中则照常 `pool.token` 重写上游并轮换
3. 池空/全耗尽→503 + `ReportEvent(exhausted)`

- [x] **Step 1: 扩展 Config**

替换 `proxy/config.go` 的 `Config` 为：

```go
type Config struct {
	Listen     string   `json:"listen"`
	Keys       []string `json:"keys,omitempty"` // 可选本地兜底；Pulse 模式下由池覆盖
	PulseURL   string   `json:"pulse_url"`
	PulseToken string   `json:"pulse_token"`
	PoolEvery  string   `json:"pool_every"` // e.g. "60s"
}
```

`loadConfig` / `save` 保持不变（JSON 自动兼容）。

- [x] **Step 2: Server 挂上 pulse + sessions**

`proxy/server.go` 的 `Server` 增加字段：

```go
	pulse    *PulseClient
	sessions *SessionMap
	onRotate func(entry *keyEntry, binding SessionBinding, kind failKind)
```

`NewServer` 签名改为：

```go
func NewServer(pool *Pool, ca *CA, pulse *PulseClient, sessions *SessionMap) *Server {
	s := &Server{
		pool: pool, ca: ca, pulse: pulse, sessions: sessions,
		transport: &http.Transport{
			ForceAttemptHTTP2:   true,
			TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
			MaxIdleConnsPerHost: 16,
		},
		shouldMITM: defaultShouldMITM,
	}
	return s
}
```

同步改 `proxy_test.go` 里 `NewServer(pool, ca)` → `NewServer(pool, ca, nil, NewSessionMap())`。

- [x] **Step 3: mitm 拦截 exchange**

在 `handleMITM` 开头、准备 body 之前插入：

```go
	if req.Method == http.MethodPost && req.URL.Path == exchangePath {
		s.handleExchange(w, req)
		return
	}
```

删除（或不再依赖）`skipAuth := strings.HasPrefix(req.URL.Path, "/auth/")` 对 exchange 的透传；其他 `/auth/*` 仍可透传（若有）。

实现 `handleExchange`（同文件）：

```go
func (s *Server) handleExchange(w http.ResponseWriter, req *http.Request) {
	defer req.Body.Close()
	io.Copy(io.Discard, io.LimitReader(req.Body, 1<<20))

	auth := strings.TrimSpace(req.Header.Get("Authorization"))
	pulseKey := strings.TrimPrefix(auth, "Bearer ")
	pulseKey = strings.TrimPrefix(pulseKey, "bearer ")
	if pulseKey == "" || pulseKey == auth {
		http.Error(w, "missing pulse key", http.StatusUnauthorized)
		return
	}
	if s.pulse == nil {
		http.Error(w, "pulse client not configured", http.StatusServiceUnavailable)
		return
	}
	res, err := s.pulse.Authorize(pulseKey)
	if err != nil {
		log.Printf("[mitm] authorize fail-closed: %v", err)
		http.Error(w, "authorize unavailable", http.StatusServiceUnavailable)
		return
	}
	switch res.Status {
	case "invalid":
		http.Error(w, "invalid pulse key", http.StatusUnauthorized)
		return
	case "suspended":
		msg := "suspended"
		if res.Reason != nil {
			msg = *res.Reason
		}
		http.Error(w, msg, http.StatusForbidden)
		return
	case "window_limited":
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusTooManyRequests)
		_, _ = w.Write([]byte(`{"code":"resource_exhausted","message":"5h window limited; retry later"}`))
		return
	case "ok":
		// continue
	default:
		http.Error(w, "authorize rejected", http.StatusForbidden)
		return
	}

	entry, token, err := s.pool.token(req.Context())
	if err != nil {
		if s.pulse != nil {
			s.pulse.ReportEvent(EventItem{EventType: "exhausted", ProxyKeyID: res.ProxyKeyID, Detail: err.Error()})
		}
		http.Error(w, "cursor-pulse-proxy: all API keys exhausted", http.StatusServiceUnavailable)
		return
	}
	s.sessions.Bind(token, SessionBinding{ProxyKeyID: res.ProxyKeyID, PulseKey: pulseKey})
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"accessToken":  token,
		"refreshToken": "pulse",
	})
	log.Printf("[mitm] exchange ok proxy_key=%s credential=%s", res.ProxyKeyID, entry.credentialID)
}
```

（补上 `"encoding/json"` import。）

业务请求路径：在拿到 `bodyFor` 之后、attempt 循环前：

```go
	var binding SessionBinding
	if s.sessions != nil {
		cliTok := strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer ")
		b, ok := s.sessions.Lookup(cliTok)
		if !ok {
			http.Error(w, "session expired; re-exchange", http.StatusUnauthorized)
			return
		}
		binding = b
	}
```

在 `s.mark(...)` 调用处，若配置了 `onRotate` / pulse，上报 `rotation` 事件（带 `binding.ProxyKeyID` 与 `entry.credentialID`）。

循环内重写 Authorization 逻辑保持：始终用 `pool.token()` 的上游 JWT（与基线一致）；归属只靠 agent 持有的已绑定 JWT。

- [x] **Step 4: 改 `main.go` 装配**

关键变更：

- flags 增加 `-pulse-url` `-pulse-token`；也从环境变量 `PULSE_BASE_URL` / `PULSE_INTERNAL_SERVICE_TOKEN` 读取（flag 优先）。
- 若 `PulseURL`+`PulseToken` 非空：允许无本地 keys；`NewPoolFromCredentials(nil)` 起步，后台每 60s `FetchPool`→`ReplaceFromPulse`。
- 若无 Pulse 且无 keys：仍 exit 2（本地开发兜底）。
- `NewServer(pool, ca, pulse, sessions)`；`pulse.Start()`；进程退出前 `pulse.Stop()`（可用简单 signal，或先 defer Stop）。

伪代码骨架：

```go
	pulseURL := envOrFlag(...)
	pulseTok := envOrFlag(...)
	var pulse *PulseClient
	sessions := NewSessionMap()
	var pool *Pool
	if pulseURL != "" && pulseTok != "" {
		pulse = NewPulseClient(pulseURL, pulseTok, 60*time.Second)
		pulse.Start()
		defer pulse.Stop()
		pool = NewPoolFromCredentials(nil)
		go pollPool(pool, pulse, 60*time.Second)
	} else {
		pool = NewPool(cfg.Keys)
	}
	srv := NewServer(pool, ca, pulse, sessions)
```

```go
func pollPool(pool *Pool, pulse *PulseClient, every time.Duration) {
	tick := time.NewTicker(every)
	defer tick.Stop()
	refresh := func() {
		creds, err := pulse.FetchPool()
		if err != nil {
			log.Printf("[pool] fetch: %v", err)
			return
		}
		pool.ReplaceFromPulse(creds)
	}
	refresh()
	for range tick.C {
		refresh()
	}
}
```

- [x] **Step 5: exchange 集成测试**

创建 `proxy/exchange_test.go`：用假 Pulse（authorize ok / suspended）+ 假上游 exchange + `newTestProxy` 变体（注入 pulse）。验证：

1. Bearer `pk_ok` → 200 + accessToken，且 sessions 可 Lookup
2. Bearer `pk_bad` suspended → 403
3. 业务请求无映射 JWT → 401

（可复制 `proxy_test.go` 的 CONNECT 客户端辅助函数。）

- [x] **Step 6: 跑全量 Go 测试**

Run: `cd proxy && go test ./... -count=1`
Expected: PASS

- [x] **Step 7: 提交**

```bash
git add proxy/config.go proxy/main.go proxy/server.go proxy/mitm.go proxy/proxy_test.go proxy/exchange_test.go
git commit -m "feat(proxy): intercept exchange via Pulse authorize and bind JWT sessions"
```

---

### Task 5: 用量旁路 `usage_tap.go`

**Files:**
- Create: `proxy/usage_tap.go`
- Create: `proxy/usage_tap_test.go`

算法移植自 `D:\projects\cursor-proxy\src\proto.js`：

- 递归找 length-delimited field **14**，子消息 varint fields 1–5 → input/output/cache_read/cache_write/reasoning

- [x] **Step 1: 写失败测试**

创建 `proxy/usage_tap_test.go`：

```go
package main

import "testing"

func varint(n int) []byte {
	var b []byte
	v := uint64(n)
	for {
		c := byte(v & 0x7f)
		v >>= 7
		if v != 0 {
			c |= 0x80
		}
		b = append(b, c)
		if v == 0 {
			break
		}
	}
	return b
}

func varintField(no, value int) []byte {
	return append(varint((no<<3)|0), varint(value)...)
}

func msgField(no int, payload []byte) []byte {
	out := append(varint((no<<3)|2), varint(len(payload))...)
	return append(out, payload...)
}

func TestFindTurnEnded(t *testing.T) {
	// ServerMessage{1: InteractionUpdate{14: TurnEndedUpdate{1:1234,2:56,5:7}}}
	inner := append(append(varintField(1, 1234), varintField(2, 56)...), varintField(5, 7)...)
	payload := msgField(1, msgField(14, inner))
	tok := findTurnEnded(payload)
	if tok == nil || tok.Input != 1234 || tok.Output != 56 || tok.Reasoning != 7 {
		t.Fatalf("%+v", tok)
	}
	if findTurnEnded([]byte("plain")) != nil {
		t.Fatal("expected nil")
	}
}
```

- [x] **Step 2: 跑测试确认失败**

Run: `cd proxy && go test -run TestFindTurnEnded -count=1`
Expected: FAIL

- [x] **Step 3: 实现**

创建 `proxy/usage_tap.go`：

```go
package main

// findTurnEnded best-effort extracts TurnEndedUpdate token counts from a
// protobuf payload at any nesting depth (agent.v1 InteractionUpdate field 14).
// Fields: 1 input, 2 output, 3 cache_read, 4 cache_write, 5 reasoning.
func findTurnEnded(buf []byte) *TokenCounts {
	return findTurnEndedDepth(buf, 0)
}

func findTurnEndedDepth(buf []byte, depth int) *TokenCounts {
	if depth > 8 {
		return nil
	}
	for _, f := range iterProtoFields(buf) {
		if f.wire != 2 {
			continue
		}
		if f.fieldNo == 14 {
			if tok := looksLikeTurnEnded(f.bytes); tok != nil {
				return tok
			}
		}
		if nested := findTurnEndedDepth(f.bytes, depth+1); nested != nil {
			return nested
		}
	}
	return nil
}

type protoField struct {
	fieldNo int
	wire    int
	varint  uint64
	bytes   []byte
}

func iterProtoFields(buf []byte) []protoField {
	var out []protoField
	i := 0
	for i < len(buf) {
		tag, n := readUvarint(buf[i:])
		if n <= 0 {
			break
		}
		i += n
		fieldNo := int(tag >> 3)
		wire := int(tag & 7)
		if fieldNo == 0 {
			break
		}
		switch wire {
		case 0:
			v, n := readUvarint(buf[i:])
			if n <= 0 {
				return out
			}
			i += n
			out = append(out, protoField{fieldNo: fieldNo, wire: wire, varint: v})
		case 2:
			l, n := readUvarint(buf[i:])
			if n <= 0 || i+n+int(l) > len(buf) {
				return out
			}
			start := i + n
			end := start + int(l)
			out = append(out, protoField{fieldNo: fieldNo, wire: wire, bytes: buf[start:end]})
			i = end
		case 5:
			if i+4 > len(buf) {
				return out
			}
			i += 4
			out = append(out, protoField{fieldNo: fieldNo, wire: wire})
		case 1:
			if i+8 > len(buf) {
				return out
			}
			i += 8
			out = append(out, protoField{fieldNo: fieldNo, wire: wire})
		default:
			return out
		}
	}
	return out
}

func readUvarint(b []byte) (uint64, int) {
	var x uint64
	var s uint
	for i := 0; i < len(b) && i < 10; i++ {
		c := b[i]
		if c < 0x80 {
			if i == 9 && c > 1 {
				return 0, -1
			}
			return x | uint64(c)<<s, i + 1
		}
		x |= uint64(c&0x7f) << s
		s += 7
	}
	return 0, -1
}

func looksLikeTurnEnded(buf []byte) *TokenCounts {
	tok := &TokenCounts{}
	found := false
	for _, f := range iterProtoFields(buf) {
		if f.wire != 0 || f.fieldNo < 1 || f.fieldNo > 5 {
			return nil
		}
		found = true
		switch f.fieldNo {
		case 1:
			tok.Input = int64(f.varint)
		case 2:
			tok.Output = int64(f.varint)
		case 3:
			tok.CacheRead = int64(f.varint)
		case 4:
			tok.CacheWrite = int64(f.varint)
		case 5:
			tok.Reasoning = int64(f.varint)
		}
	}
	if !found || (tok.Input == 0 && tok.Output == 0) {
		// allow reasoning-only? Node requires field 1 or 2 present.
		if !found {
			return nil
		}
		// if only reasoning set, still accept when field 1 or 2 was seen as 0 explicitly — Node checks !== undefined.
		// We accept any found fields 1-5.
	}
	if !found {
		return nil
	}
	// Match Node: require field 1 or 2 present (including zero). Track via re-scan:
	has12 := false
	for _, f := range iterProtoFields(buf) {
		if f.fieldNo == 1 || f.fieldNo == 2 {
			has12 = true
		}
	}
	if !has12 {
		return nil
	}
	return tok
}
```

- [x] **Step 4: 跑测试通过**

Run: `cd proxy && go test -run TestFindTurnEnded -count=1`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add proxy/usage_tap.go proxy/usage_tap_test.go
git commit -m "feat(proxy): best-effort TurnEndedUpdate token tap"
```

---

### Task 6: 将 usage tap 接入流式转发 + 事件

**Files:**
- Modify: `proxy/mitm.go`
- Modify: `proxy/exchange_test.go` 或新建 `proxy/usage_e2e_test.go`

- [x] **Step 1: 流式路径旁路**

在 `handleMITM` 的 Connect 流式成功分支（已 `writeEnvelope` 首帧并 `Flush` 之后），将：

```go
			io.Copy(flushWriter{w: w}, resp.Body)
```

改为 tee 解析（解析失败不影响转发）：

```go
			tap := &usageTapWriter{w: flushWriter{w: w}, onTokens: func(tc TokenCounts) {
				if s.pulse == nil || binding.ProxyKeyID == "" {
					return
				}
				s.pulse.EnqueueUsage(UsageItem{
					ProxyKeyID:   binding.ProxyKeyID,
					CredentialID: entry.credentialID,
					Tokens:       tc,
				})
			}}
			io.Copy(tap, resp.Body)
```

实现 `usageTapWriter`（可放 `usage_tap.go`）：

```go
type usageTapWriter struct {
	w        io.Writer
	buf      []byte
	onTokens func(TokenCounts)
}

func (t *usageTapWriter) Write(p []byte) (int, error) {
	n, err := t.w.Write(p)
	t.buf = append(t.buf, p[:n]...)
	// drain complete connect envelopes from buf
	for {
		if len(t.buf) < 5 {
			break
		}
		size := int(binary.BigEndian.Uint32(t.buf[1:5]))
		total := 5 + size
		if size < 0 || total > len(t.buf) {
			break
		}
		payload := t.buf[5:total]
		t.buf = t.buf[total:]
		if tok := findTurnEnded(payload); tok != nil && t.onTokens != nil {
			t.onTokens(*tok)
		}
	}
	return n, err
}
```

（import `encoding/binary`。）

首帧 data envelope 在 flush 前也应尝试 `findTurnEnded`（通常 TurnEnded 在后续帧）。

- [x] **Step 2: 换号事件**

改 `Server.mark`：

```go
func (s *Server) mark(entry *keyEntry, kind failKind, binding SessionBinding) {
	if kind == failAuth {
		s.pool.markBad(entry)
	} else {
		s.pool.markExhausted(entry)
	}
	if s.pulse != nil {
		s.pulse.ReportEvent(EventItem{
			EventType:    "rotation",
			ProxyKeyID:   binding.ProxyKeyID,
			CredentialID: entry.credentialID,
			Detail:       kind.String(),
		})
	}
}
```

更新所有 `s.mark(entry, kind)` 调用传入 `binding`。

- [x] **Step 3: e2e 测用量入队**

在假上游 Run 响应中塞入含 TurnEnded 的 data envelope；断言 `PulseClient` 收到 usage POST（可用 atomic + httptest）。

- [x] **Step 4: 全量测试**

Run: `cd proxy && go test ./... -count=1`
Expected: PASS

- [x] **Step 5: 提交**

```bash
git add proxy/mitm.go proxy/usage_tap.go proxy/usage_e2e_test.go
git commit -m "feat(proxy): stream usage tap and rotation events to Pulse"
```

---

### Task 7: README 与本地手工冒烟清单

**Files:**
- Modify: `proxy/README.md`

- [x] **Step 1: 重写 README 关键段落**

写明：

1. 构建：`go build -o cursor-pulse-proxy.exe .`
2. 启动（Pulse 模式）：

```powershell
$env:PULSE_BASE_URL = "http://127.0.0.1:8080"
$env:PULSE_INTERNAL_SERVICE_TOKEN = "pulse-internal-dev"
.\cursor-pulse-proxy.exe -listen 127.0.0.1:8317
```

3. 客户端：

```powershell
$env:HTTPS_PROXY = "http://127.0.0.1:8317"
cursor-agent -k
# 登录/API key 处填 Pulse 签发的 pk_...
```

4. 与控制面联调检查表：admin 开 `proxy_enabled` → 池非空 → 创建脉冲 key → authorize 冒烟 → agent 跑一条 → web-admin 用量抽屉有记录。

5. 明确：**本计划不修改 `cursor-pulse.bat`**（计划 3）。

- [x] **Step 2: 提交**

```bash
git add proxy/README.md
git commit -m "docs(proxy): Pulse-mode runbook for Go data plane"
```

---

### Task 8: 回归闸门

**Files:** 无强制新增

- [x] **Step 1: Go 全量**

Run: `cd proxy && go test ./... -count=1`
Expected: 全部 PASS

- [x] **Step 2: 控制面 Proxy 测试未被破坏**

Run（仓库根目录）：

```powershell
python -m pytest tests/test_proxy_models.py tests/test_proxy_keys.py tests/test_proxy_service.py tests/test_web_internal_proxy.py tests/test_web_proxy_admin.py -q
```

Expected: 全部 PASS

- [x] **Step 3: 可选联调冒烟**（本地服务已起时）

```powershell
# Pulse authorize（已有）
# 启动 proxy 后看日志：[pool] hot-updated: N credential(s)
```

- [x] **Step 4: 若有小修则提交**

```bash
git add -A
git commit -m "chore(proxy): data-plane regression fixes"
```

（无变更则跳过。）

---

## 自审记录

- **Spec 覆盖**：§5 `pulse_client` → Task 2；`pool` 热更新 → Task 3；`mitm` exchange 拦截/映射 → Task 4；`usage_tap` → Task 5–6；错误表 §9 → Task 4/6；测试 §10 Go 假上游 → Task 1/4/6/8。部署 §11-3 → **明确排除**（计划 3）。
- **控制面契约**：authorize/pool/usage/events 路径与 Bearer token 对齐 `pulse/web/internal_proxy_api.py`；usage tokens 键名 `input/output/cache_read/cache_write/reasoning`。
- **偏离说明**：基线本地 `-keys` 模式保留作无 Pulse 时的开发兜底；生产路径以 Pulse pool 为准。go.mod 钉 `go 1.22`（基线曾写 1.26）。
- **占位符扫描**：无 TBD/TODO；各 Task 含可运行命令与期望结果。
