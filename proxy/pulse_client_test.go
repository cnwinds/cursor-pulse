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

func TestPulseClientAuthorizeLoanAliasNotCached(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status":         "ok",
			"mode":           "loan_alias",
			"loan_id":        "loan-1",
			"credential_id":  "cred-1",
			"cursor_api_key": "crsr_secret",
			"reason":         nil,
		})
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	a1, err := c.Authorize("pka_abc")
	if err != nil || a1.Mode != "loan_alias" || a1.CursorAPIKey != "crsr_secret" {
		t.Fatalf("a1=%+v err=%v", a1, err)
	}
	a2, err := c.Authorize("pka_abc")
	if err != nil || hits.Load() != 2 {
		t.Fatalf("loan_alias must not cache: hits=%d err=%v a2=%+v", hits.Load(), err, a2)
	}
	if len(c.authCache) != 0 {
		t.Fatalf("authCache should stay empty for loan_alias, got %+v", c.authCache)
	}
}

func TestPulseClientAuthorizeLoanPoolNotCached(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status":  "ok",
			"mode":    "loan_pool",
			"loan_id": "loan-pool-1",
			"reason":  nil,
		})
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	if _, err := c.Authorize("pka_pool"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Authorize("pka_pool"); err != nil {
		t.Fatal(err)
	}
	if hits.Load() != 2 {
		t.Fatalf("loan_pool must not cache: hits=%d", hits.Load())
	}
}

func TestAuthorizeSeatSendsSlotAndBypassesCache(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		var body struct {
			PulseKey            string   `json:"pulse_key"`
			CurrentCredentialID string   `json:"current_credential_id"`
			ReleaseCurrent      bool     `json:"release_current"`
			QuotaPool           string   `json:"quota_pool"`
			Held                []string `json:"held_credential_ids"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode: %v", err)
		}
		switch hits.Load() {
		case 1:
			if body.CurrentCredentialID != "" || body.ReleaseCurrent || body.QuotaPool != "" || body.Held != nil {
				t.Fatalf("plain authorize should omit seat fields: %+v", body)
			}
		case 2:
			if body.QuotaPool != "auto" || body.CurrentCredentialID != "" || body.Held != nil {
				t.Fatalf("exchange seat should ask auto with no current: %+v", body)
			}
		case 3:
			if body.QuotaPool != "api" || body.CurrentCredentialID != "cred-9" || !body.ReleaseCurrent {
				t.Fatalf("api slot release: %+v", body)
			}
			if len(body.Held) != 2 || body.Held[0] != "cred-9" || body.Held[1] != "cred-auto" {
				t.Fatalf("other slot credentials should be held, blanks dropped: %+v", body.Held)
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "proxy_key_id": "pk1", "mode": "quota", "reason": nil,
			"seat_advised": true, "assigned_credential_id": "cred-9",
		})
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	if _, err := c.Authorize("pk_abc"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Authorize("pk_abc"); err != nil || hits.Load() != 1 {
		t.Fatalf("cache hits=%d", hits.Load())
	}
	res, err := c.AuthorizeSeat("pk_abc", "", false, quotaPoolAuto, nil)
	if err != nil || !res.SeatAdvised || res.AssignedCredentialID != "cred-9" || hits.Load() != 2 {
		t.Fatalf("seat res=%+v hits=%d err=%v", res, hits.Load(), err)
	}
	if _, err := c.AuthorizeSeat("pk_abc", "cred-9", true, quotaPoolAPI, []string{"cred-9", "cred-auto", ""}); err != nil || hits.Load() != 3 {
		t.Fatalf("release hits=%d err=%v", hits.Load(), err)
	}
}

func TestAuthorizeFreshBypassesCache(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		hits.Add(1)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "proxy_key_id": "pk1", "mode": "quota", "reason": nil,
		})
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	if _, err := c.Authorize("pk_x"); err != nil {
		t.Fatal(err)
	}
	if _, err := c.Authorize("pk_x"); err != nil || hits.Load() != 1 {
		t.Fatalf("cached authorize hits=%d", hits.Load())
	}
	if _, err := c.AuthorizeFresh("pk_x"); err != nil || hits.Load() != 2 {
		t.Fatalf("AuthorizeFresh should bypass cache hits=%d err=%v", hits.Load(), err)
	}
}

func TestAuthorizeDoesNotCacheRejects(t *testing.T) {
	var hits atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := hits.Add(1)
		status := "invalid"
		if n >= 2 {
			status = "ok"
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": status, "proxy_key_id": "pk1", "mode": "quota", "reason": nil,
		})
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	res, err := c.Authorize("pk_recreate")
	if err != nil || res.Status != "invalid" {
		t.Fatalf("first: %+v err=%v", res, err)
	}
	res, err = c.Authorize("pk_recreate")
	if err != nil || res.Status != "ok" || hits.Load() != 2 {
		t.Fatalf("reject must not cache: %+v hits=%d err=%v", res, hits.Load(), err)
	}
}

func TestExchangeConflicts(t *testing.T) {
	sameLoan := SessionBinding{Mode: "loan_pool", LoanID: "loan-a"}
	if exchangeConflicts(sameLoan, "loan_pool", "", "loan-a") {
		t.Fatal("same loan_pool may rebind")
	}
	otherLoan := SessionBinding{Mode: "loan_pool", LoanID: "loan-b"}
	if !exchangeConflicts(otherLoan, "loan_pool", "", "loan-a") {
		t.Fatal("different loan_pool must conflict")
	}
	pk := SessionBinding{ProxyKeyID: "pk1"}
	if !exchangeConflicts(pk, "loan_pool", "", "loan-a") {
		t.Fatal("pk_ holder must conflict with loan_pool")
	}
	if exchangeConflicts(pk, "quota", "pk1", "") {
		t.Fatal("same proxy key may rebind")
	}
	if !exchangeConflicts(sameLoan, "quota", "pk1", "") {
		t.Fatal("loan_pool holder must conflict with pk_")
	}
	if exchangeConflicts(SessionBinding{}, "loan_pool", "", "loan-a") {
		t.Fatal("empty binding does not conflict")
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
	snap, err := c.FetchPool()
	if err != nil || len(snap.Default) != 1 || snap.Default[0].CredentialID != "c1" {
		t.Fatalf("%+v %v", snap, err)
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

func TestPulseClientUsageFlushRequeuesOnFailure(t *testing.T) {
	var posts atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		posts.Add(1)
		w.WriteHeader(http.StatusBadGateway)
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	c.usageMaxRetries = 2
	c.usageBatchMax = 10
	c.usageBufMax = 100
	c.EnqueueUsage(UsageItem{ProxyKeyID: "pk1", Tokens: TokenCounts{Input: 1}})
	c.EnqueueUsage(UsageItem{ProxyKeyID: "pk1", Tokens: TokenCounts{Input: 2}})
	c.flushUsage(false)

	if posts.Load() < 2 {
		t.Fatalf("expected retries, got %d posts", posts.Load())
	}
	c.usageMu.Lock()
	n := len(c.usageBuf)
	backoff := c.usageRetryAfter
	c.usageMu.Unlock()
	if n != 2 {
		t.Fatalf("expected 2 items requeued, got %d", n)
	}
	if backoff.IsZero() || !time.Now().Before(backoff) {
		t.Fatalf("expected usageRetryAfter in the future, got %v", backoff)
	}

	// Backoff should skip a non-force flush without draining the buffer.
	before := posts.Load()
	c.flushUsage(false)
	if posts.Load() != before {
		t.Fatalf("backoff flush should not POST; before=%d after=%d", before, posts.Load())
	}

	// Force flush ignores backoff and still requeues on failure (shutdown path).
	c.flushUsage(true)
	c.usageMu.Lock()
	n = len(c.usageBuf)
	c.usageMu.Unlock()
	if n != 2 {
		t.Fatalf("force flush failure should still requeue; got %d", n)
	}
}

func TestPulseClientUsageRequeueRespectsBufMax(t *testing.T) {
	c := NewPulseClient("http://127.0.0.1:1", "tok", time.Minute)
	c.usageBufMax = 3
	c.requeueUsage([]UsageItem{
		{ProxyKeyID: "a", Tokens: TokenCounts{Input: 1}},
		{ProxyKeyID: "b", Tokens: TokenCounts{Input: 2}},
		{ProxyKeyID: "c", Tokens: TokenCounts{Input: 3}},
		{ProxyKeyID: "d", Tokens: TokenCounts{Input: 4}},
	}, false)
	c.usageMu.Lock()
	defer c.usageMu.Unlock()
	if len(c.usageBuf) != 3 {
		t.Fatalf("len=%d want 3", len(c.usageBuf))
	}
	if c.usageBuf[0].ProxyKeyID != "a" || c.usageBuf[2].ProxyKeyID != "c" {
		t.Fatalf("should keep oldest, got %+v", c.usageBuf)
	}
}

func TestPulseClientStartStopLifecycle(t *testing.T) {
	var bodies atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/usage" {
			t.Fatalf("path %s", r.URL.Path)
		}
		bodies.Add(1)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
	}))
	defer srv.Close()

	c := NewPulseClient(srv.URL, "tok", time.Minute)
	c.usageBatchMax = 100
	c.usageFlushEvery = time.Hour
	c.Start()
	c.Start() // second Start is no-op

	c.EnqueueUsage(UsageItem{ProxyKeyID: "pk1", Tokens: TokenCounts{Input: 1}})
	c.Stop()

	if bodies.Load() < 1 {
		t.Fatalf("expected flush on stop, got %d usage POSTs", bodies.Load())
	}

	before := bodies.Load()
	c.EnqueueUsage(UsageItem{ProxyKeyID: "pk2", Tokens: TokenCounts{Input: 99}})
	c.Stop() // idempotent second Stop
	if bodies.Load() != before {
		t.Fatalf("EnqueueUsage after Stop should not flush; posts before=%d after=%d", before, bodies.Load())
	}
}
