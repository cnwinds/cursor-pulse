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
	"testing"
	"time"
)

func newFakePulse(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body struct {
			PulseKey string `json:"pulse_key"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		switch body.PulseKey {
		case "pk_ok":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk1", "mode": "quota", "reason": nil,
			})
		case "pk_ok2":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk2", "mode": "quota", "reason": nil,
			})
		case "pk_bad":
			reason := "account suspended"
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "suspended", "proxy_key_id": "pk2", "mode": "quota", "reason": reason,
			})
		default:
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "invalid", "proxy_key_id": "", "mode": "", "reason": nil,
			})
		}
	}))
	t.Cleanup(srv.Close)
	return srv
}

// newPulseTestProxy starts a proxy with Pulse + SessionMap against a fake upstream.
func newPulseTestProxy(t *testing.T, fu *fakeUpstream, pulseURL string) (addr string, caPEM []byte, sessions *SessionMap) {
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

func TestExchangeOKBindsSession(t *testing.T) {
	fu := newFakeUpstream(t)
	pulse := newFakePulse(t)
	proxyAddr, caPEM, sessions := newPulseTestProxy(t, fu, pulse.URL)
	client := connectClient(t, proxyAddr, caPEM)

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	req, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer pk_ok")
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
	var out struct {
		AccessToken  string `json:"accessToken"`
		RefreshToken string `json:"refreshToken"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if out.AccessToken == "" {
		t.Fatal("missing accessToken")
	}
	b, ok := sessions.Lookup(out.AccessToken)
	if !ok || b.ProxyKeyID != "pk1" || b.PulseKey != "pk_ok" {
		t.Fatalf("session bind failed: ok=%v %+v", ok, b)
	}
}

func TestExchangeSuspended(t *testing.T) {
	fu := newFakeUpstream(t)
	pulse := newFakePulse(t)
	proxyAddr, caPEM, _ := newPulseTestProxy(t, fu, pulse.URL)
	client := connectClient(t, proxyAddr, caPEM)

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	req, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer pk_bad")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusForbidden {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
}

func TestBusinessUnknownJWTUnauthorized(t *testing.T) {
	fu := newFakeUpstream(t)
	pulse := newFakePulse(t)
	proxyAddr, caPEM, _ := newPulseTestProxy(t, fu, pulse.URL)
	client := connectClient(t, proxyAddr, caPEM)

	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")
	req, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/aiserver.v1.TestService/Unary", bytes.NewReader([]byte{0x0A}))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer unknown-jwt")
	req.Header.Set("Content-Type", "application/proto")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("status %d body %s", resp.StatusCode, b)
	}
}

// TestExchangeCollisionRotatesToOtherCredential ensures that when a pool
// credential returns a JWT already bound to a different ProxyKeyID, exchange
// skips that credential and mints from another pool key instead of failing.
func TestExchangeCollisionRotatesToOtherCredential(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		tok := "jwt-shared"
		if key == "keyB" {
			tok = "jwt-B"
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  tok,
			"refreshToken": "r",
		})
	})
	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)
	fu := &fakeUpstream{Server: fuSrv}

	pulse := newFakePulse(t)

	pool := NewPool([]string{"keyA", "keyB"})
	pool.exchangeBase = fu.URL
	pool.client = fu.Client()

	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionMap()
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), sessions)
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

	caPEM, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatal(err)
	}
	client := connectClient(t, ln.Addr().String(), caPEM)
	upstreamAddr := strings.TrimPrefix(fu.URL, "https://")

	doExchange := func(pulseKey string) string {
		t.Helper()
		req, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+pulseKey)
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			b, _ := io.ReadAll(resp.Body)
			t.Fatalf("%s: status %d body %s", pulseKey, resp.StatusCode, b)
		}
		var out struct {
			AccessToken string `json:"accessToken"`
		}
		if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
			t.Fatal(err)
		}
		return out.AccessToken
	}

	tok1 := doExchange("pk_ok")
	tok2 := doExchange("pk_ok2")

	if tok1 != "jwt-shared" {
		t.Fatalf("first token: got %q want jwt-shared", tok1)
	}
	if tok2 != "jwt-B" {
		t.Fatalf("second token: got %q want jwt-B (collision rotate)", tok2)
	}

	b1, ok := sessions.Lookup("jwt-shared")
	if !ok || b1.ProxyKeyID != "pk1" {
		t.Fatalf("jwt-shared binding: ok=%v %+v", ok, b1)
	}
	b2, ok := sessions.Lookup("jwt-B")
	if !ok || b2.ProxyKeyID != "pk2" {
		t.Fatalf("jwt-B binding: ok=%v %+v", ok, b2)
	}
}
