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
