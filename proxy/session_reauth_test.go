package main

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestSessionMapDelete(t *testing.T) {
	m := NewSessionMap()
	m.Bind("jwt1", SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_abc"})
	m.Delete("jwt1")
	if _, ok := m.Lookup("jwt1"); ok {
		t.Fatal("expected session deleted")
	}
	m.Delete("missing") // no panic
}

func TestSessionBindSetsBoundAt(t *testing.T) {
	m := NewSessionMap()
	before := time.Now()
	m.Bind("jwt1", SessionBinding{ProxyKeyID: "pk1", PulseKey: "pk_abc"})
	b, ok := m.Lookup("jwt1")
	if !ok || b.BoundAt.Before(before) || b.BoundAt.After(time.Now()) {
		t.Fatalf("BoundAt not set: %+v", b)
	}
}

func TestSessionWithinTTLSkipsReauthorize(t *testing.T) {
	fu := newFakeUpstream(t)
	var authCalls atomic.Int32
	pulse := newCountingFakePulse(t, &authCalls, map[string]string{"pk_ok": "ok"})
	proxyAddr, caPEM, sessions := newPulseTestProxyWithTTL(t, fu, pulse.URL, 2*time.Minute)
	client := connectClient(t, proxyAddr, caPEM)

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	exReq, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	exReq.Header.Set("Authorization", "Bearer pk_ok")
	exReq.Header.Set("Content-Type", "application/json")
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	exResp.Body.Close()
	if exResp.StatusCode != http.StatusOK {
		t.Fatalf("exchange status %d", exResp.StatusCode)
	}
	callsAfterExchange := authCalls.Load()

	bizReq, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/aiserver.v1.TestService/Unary", bytes.NewReader([]byte{0x0A}))
	if err != nil {
		t.Fatal(err)
	}
	// Use bound JWT from pool exchange path — bind manually with fresh BoundAt.
	sessions.Bind("jwt-fresh", SessionBinding{
		ProxyKeyID: "pk1",
		PulseKey:   "pk_ok",
		BoundAt:    time.Now(),
	})
	bizReq.Header.Set("Authorization", "Bearer jwt-fresh")
	bizReq.Header.Set("Content-Type", "application/proto")
	bizResp, err := client.Do(bizReq)
	if err != nil {
		t.Fatal(err)
	}
	defer bizResp.Body.Close()
	if bizResp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(bizResp.Body)
		t.Fatalf("business status %d body %s", bizResp.StatusCode, b)
	}
	if authCalls.Load() != callsAfterExchange {
		t.Fatalf("authorize called within TTL: before=%d after=%d", callsAfterExchange, authCalls.Load())
	}
}

func TestSessionReauthAfterTTLSuspended(t *testing.T) {
	fu := newFakeUpstream(t)
	var authCalls atomic.Int32
	pulse := newCountingFakePulse(t, &authCalls, map[string]string{"pk_ok": "suspended"})
	proxyAddr, caPEM, sessions := newPulseTestProxyWithTTL(t, fu, pulse.URL, 50*time.Millisecond)
	client := connectClient(t, proxyAddr, caPEM)

	sessions.Bind("jwt-stale", SessionBinding{
		ProxyKeyID: "pk1",
		PulseKey:   "pk_ok",
		BoundAt:    time.Now().Add(-time.Minute),
	})

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	bizReq, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/aiserver.v1.TestService/Unary", bytes.NewReader([]byte{0x0A}))
	if err != nil {
		t.Fatal(err)
	}
	bizReq.Header.Set("Authorization", "Bearer jwt-stale")
	bizReq.Header.Set("Content-Type", "application/proto")
	bizResp, err := client.Do(bizReq)
	if err != nil {
		t.Fatal(err)
	}
	defer bizResp.Body.Close()
	if bizResp.StatusCode != http.StatusUnauthorized {
		b, _ := io.ReadAll(bizResp.Body)
		t.Fatalf("status %d body %s", bizResp.StatusCode, b)
	}
	if authCalls.Load() < 1 {
		t.Fatal("expected re-authorize after TTL")
	}
	if _, ok := sessions.Lookup("jwt-stale"); ok {
		t.Fatal("expected session unbound after suspended re-auth")
	}
}

func TestSessionReauthAfterTTLOkRefreshesBoundAt(t *testing.T) {
	fu := newFakeUpstream(t)
	var authCalls atomic.Int32
	pulse := newCountingFakePulse(t, &authCalls, map[string]string{"pk_ok": "ok"})
	proxyAddr, caPEM, sessions := newPulseTestProxyWithTTL(t, fu, pulse.URL, 50*time.Millisecond)
	client := connectClient(t, proxyAddr, caPEM)

	staleAt := time.Now().Add(-time.Minute)
	sessions.Bind("jwt-stale", SessionBinding{
		ProxyKeyID: "pk1",
		PulseKey:   "pk_ok",
		BoundAt:    staleAt,
	})

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	bizReq, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/aiserver.v1.TestService/Unary", bytes.NewReader([]byte{0x0A}))
	if err != nil {
		t.Fatal(err)
	}
	bizReq.Header.Set("Authorization", "Bearer jwt-stale")
	bizReq.Header.Set("Content-Type", "application/proto")
	bizResp, err := client.Do(bizReq)
	if err != nil {
		t.Fatal(err)
	}
	bizResp.Body.Close()
	if bizResp.StatusCode != http.StatusOK {
		t.Fatalf("status %d", bizResp.StatusCode)
	}
	if authCalls.Load() < 1 {
		t.Fatal("expected re-authorize after TTL")
	}
	b, ok := sessions.Lookup("jwt-stale")
	if !ok {
		t.Fatal("session should remain bound after ok re-auth")
	}
	if !b.BoundAt.After(staleAt) {
		t.Fatalf("BoundAt not refreshed: was %v now %v", staleAt, b.BoundAt)
	}
}

func newCountingFakePulse(t *testing.T, calls *atomic.Int32, keyStatus map[string]string) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		calls.Add(1)
		var body struct {
			PulseKey string `json:"pulse_key"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		status := keyStatus[body.PulseKey]
		if status == "" {
			status = "invalid"
		}
		reason := "account suspended"
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": status, "proxy_key_id": "pk1", "mode": "quota", "reason": reason,
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

func newPulseTestProxyWithTTL(t *testing.T, fu *fakeUpstream, pulseURL string, sessionTTL time.Duration) (addr string, caPEM []byte, sessions *SessionMap) {
	t.Helper()
	pool := NewPool([]string{"keyA", "keyB"})
	pool.exchangeBase = fu.URL
	pool.client = fu.Client()

	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pulse := NewPulseClient(pulseURL, "tok", time.Minute)
	sessions = NewSessionMap()
	s := NewServer(pool, ca, pulse, sessions)
	s.sessionTTL = sessionTTL
	s.shouldMITM = func(string) bool { return true }
	s.transport = &http.Transport{
		ForceAttemptHTTP2: true,
		TLSClientConfig:   &tls.Config{InsecureSkipVerify: true},
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, s)
	t.Cleanup(func() { ln.Close() })

	pemBytes, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatal(err)
	}
	return ln.Addr().String(), pemBytes, sessions
}
