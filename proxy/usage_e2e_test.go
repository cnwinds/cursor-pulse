package main

import (
	"bytes"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"
)

func TestUsageTapStreamingE2E(t *testing.T) {
	var (
		mu          sync.Mutex
		usageBodies [][]byte
	)
	pulseSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			var body struct {
				PulseKey string `json:"pulse_key"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.PulseKey == "pk_ok" {
				_ = json.NewEncoder(w).Encode(map[string]any{
					"status": "ok", "proxy_key_id": "pk1", "mode": "quota", "reason": nil,
				})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "invalid", "proxy_key_id": "", "mode": "", "reason": nil,
			})
		case "/api/internal/v1/proxy/usage":
			b, _ := io.ReadAll(r.Body)
			mu.Lock()
			usageBodies = append(usageBodies, b)
			mu.Unlock()
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulseSrv.Close)

	inner := append(append(varintField(1, 1234), varintField(2, 56)...), varintField(5, 7)...)
	turnEnded := msgField(1, msgField(14, inner))

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if key != "keyA" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "tokA",
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/connect+proto")
		tok := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if tok != "tokA" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		// First data frame (live run); TurnEnded arrives in a later envelope.
		_ = writeEnvelope(w, 0x00, []byte{0xde, 0xad})
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		_ = writeEnvelope(w, 0x00, turnEnded)
		_ = writeEnvelope(w, endStreamFlag, []byte(`{"metadata":{}}`))
	})

	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()

	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pulse := NewPulseClient(pulseSrv.URL, "tok", time.Minute)
	pulse.usageBatchMax = 1 // flush on every EnqueueUsage
	sessions := NewSessionMap()
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

	caPEM, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatal(err)
	}
	client := connectClient(t, ln.Addr().String(), caPEM)
	upstreamAddr := strings.TrimPrefix(fuSrv.URL, "https://")

	// Exchange pulse key → pool JWT bound in SessionMap.
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
	defer exResp.Body.Close()
	if exResp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(exResp.Body)
		t.Fatalf("exchange status %d body %s", exResp.StatusCode, b)
	}
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	if err := json.NewDecoder(exResp.Body).Decode(&exOut); err != nil {
		t.Fatal(err)
	}
	if exOut.AccessToken == "" {
		t.Fatal("missing accessToken")
	}

	// Run with bound JWT; upstream streams TurnEnded → Pulse usage.
	modelProto := msgField(1, []byte("claude-4-sonnet"))
	runPayload := modelProto
	runBody := make([]byte, 5+len(runPayload))
	runBody[0] = 0
	binary.BigEndian.PutUint32(runBody[1:5], uint32(len(runPayload)))
	copy(runBody[5:], runPayload)
	runReq, err := http.NewRequest(
		http.MethodPost,
		"https://"+upstreamAddr+"/agent.v1.AgentService/Run",
		bytes.NewReader(runBody),
	)
	if err != nil {
		t.Fatal(err)
	}
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	defer runResp.Body.Close()
	if runResp.StatusCode != http.StatusOK {
		b, _ := io.ReadAll(runResp.Body)
		t.Fatalf("run status %d body %s", runResp.StatusCode, b)
	}
	_, _ = io.Copy(io.Discard, runResp.Body)

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := len(usageBodies)
		mu.Unlock()
		if n >= 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	mu.Lock()
	defer mu.Unlock()
	if len(usageBodies) < 1 {
		t.Fatal("expected Pulse usage POST")
	}
	var payload struct {
		Items []UsageItem `json:"items"`
	}
	if err := json.Unmarshal(usageBodies[0], &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Items) != 1 {
		t.Fatalf("items=%+v", payload.Items)
	}
	item := payload.Items[0]
	if item.ProxyKeyID != "pk1" {
		t.Fatalf("proxy_key_id=%q", item.ProxyKeyID)
	}
	if item.Model != "claude-4-sonnet" {
		t.Fatalf("model=%q want claude-4-sonnet", item.Model)
	}
	if item.Tokens.Input != 1234 || item.Tokens.Output != 56 || item.Tokens.Reasoning != 7 {
		t.Fatalf("tokens=%+v", item.Tokens)
	}
}
