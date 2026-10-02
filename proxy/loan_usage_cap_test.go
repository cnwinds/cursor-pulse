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
	"sync/atomic"
	"testing"
	"time"
)

func TestLoanAliasRunUsageCapLimited429(t *testing.T) {
	const aliasKey = "pka_test_alias_key_abc"
	const cursorKey = "crsr_bound_cursor_key_xyz"
	const limitedMsg = "【小脉借用】Auto 额度已用尽（测试）"
	var capHits atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+cursorKey {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-alias",
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			var body struct {
				PulseKey string `json:"pulse_key"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.PulseKey != aliasKey {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status":         "ok",
				"mode":           "loan_alias",
				"loan_id":        "loan-cap-1",
				"credential_id":  "cred-1",
				"cursor_api_key": cursorKey,
			})
		case "/api/internal/v1/proxy/loan-usage-cap":
			capHits.Add(1)
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status":  "limited",
				"reason":  "loan_usage_cap_exceeded",
				"message": limitedMsg,
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool(nil)
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()

	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	sessions := NewSessionMap()
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), sessions)
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
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

	exReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	exReq.Header.Set("Authorization", "Bearer "+aliasKey)
	exReq.Header.Set("Content-Type", "application/json")
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	defer exResp.Body.Close()
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	if err := json.NewDecoder(exResp.Body).Decode(&exOut); err != nil {
		t.Fatal(err)
	}

	modelProto := msgField(1, []byte("composer-1"))
	runBody := make([]byte, 5+len(modelProto))
	runBody[0] = 0
	binary.BigEndian.PutUint32(runBody[1:5], uint32(len(modelProto)))
	copy(runBody[5:], modelProto)
	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader(runBody))
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	defer runResp.Body.Close()
	body, _ := io.ReadAll(runResp.Body)
	if runResp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("expected 429, got %d body %s", runResp.StatusCode, body)
	}
	if !strings.Contains(string(body), limitedMsg) {
		t.Fatalf("expected message in body %s", body)
	}
	if capHits.Load() != 1 {
		t.Fatalf("expected one cap check, got %d", capHits.Load())
	}
}

func TestLoanPassthroughSkipsUsageCapCheck(t *testing.T) {
	const loanKey = "crsr_test_loan_key_abc"
	var capHits atomic.Int32

	inner := append(append(varintField(1, 1), varintField(2, 1)...), varintField(5, 0)...)
	turnEnded := msgField(1, msgField(14, inner))

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+loanKey {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-loan",
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/connect+proto")
		_ = writeEnvelope(w, 0x00, turnEnded)
		_ = writeEnvelope(w, endStreamFlag, []byte(`{"metadata":{}}`))
	})
	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/internal/v1/proxy/loan-usage-cap" {
			capHits.Add(1)
		}
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			var body struct {
				PulseKey string `json:"pulse_key"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.PulseKey != loanKey {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status":        "ok",
				"mode":          "loan_passthrough",
				"loan_id":       "loan-1",
				"credential_id": "cred-1",
			})
		case "/api/internal/v1/proxy/usage":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool(nil)
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pulseClient := NewPulseClient(pulse.URL, "tok", time.Minute)
	pulseClient.usageBatchMax = 1
	sessions := NewSessionMap()
	s := NewServer(pool, ca, pulseClient, sessions)
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
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

	caPEM, _ := os.ReadFile(caPath)
	client := connectClient(t, ln.Addr().String(), caPEM)
	upstreamAddr := strings.TrimPrefix(fuSrv.URL, "https://")

	exReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	exReq.Header.Set("Authorization", "Bearer "+loanKey)
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	defer exResp.Body.Close()
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)

	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	runResp.Body.Close()
	if capHits.Load() != 0 {
		t.Fatalf("loan_passthrough must not call usage cap, hits=%d", capHits.Load())
	}
}
