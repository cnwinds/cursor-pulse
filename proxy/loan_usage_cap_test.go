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

const spendCheckUnavailableMsg = "cursor-pulse-proxy: 用量校验暂不可用，请稍后重试"

func TestLoanAliasRunSpendCheckLimited429(t *testing.T) {
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
		case "/api/internal/v1/proxy/spend-check":
			capHits.Add(1)
			var body struct {
				ProxyKeyID string `json:"proxy_key_id"`
				LoanID     string `json:"loan_id"`
				Model      string `json:"model"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.LoanID != "loan-cap-1" {
				t.Errorf("spend-check loan_id=%q", body.LoanID)
			}
			if body.Model != "composer-1" {
				t.Errorf("spend-check model=%q", body.Model)
			}
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
		t.Fatalf("expected one spend check, got %d", capHits.Load())
	}
}

func TestQuotaExchangeRunSpendCheckLimited429(t *testing.T) {
	const pkKey = "pk_quota_test_key"
	const limitedMsg = "【小脉】5 小时额度已用尽"
	var capHits atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer keyA" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-quota",
			"refreshToken": "r",
		})
	})
	var upstreamRuns atomic.Int32
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		upstreamRuns.Add(1)
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
			if body.PulseKey != pkKey {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-quota-1", "mode": "quota",
			})
		case "/api/internal/v1/proxy/spend-check":
			capHits.Add(1)
			var body struct {
				ProxyKeyID string `json:"proxy_key_id"`
				LoanID     string `json:"loan_id"`
				Model      string `json:"model"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.ProxyKeyID != "pk-quota-1" {
				t.Errorf("spend-check proxy_key_id=%q", body.ProxyKeyID)
			}
			if body.LoanID != "" {
				t.Errorf("spend-check loan_id=%q want empty", body.LoanID)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "limited", "message": limitedMsg,
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), NewSessionMap())
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
	exReq.Header.Set("Authorization", "Bearer "+pkKey)
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)
	exResp.Body.Close()

	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(runResp.Body)
	runResp.Body.Close()
	if runResp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("expected 429, got %d body %s", runResp.StatusCode, body)
	}
	if !strings.Contains(string(body), limitedMsg) {
		t.Fatalf("body %s", body)
	}
	if capHits.Load() != 1 {
		t.Fatalf("spend checks %d", capHits.Load())
	}
	if upstreamRuns.Load() != 0 {
		t.Fatalf("upstream must not be called, got %d", upstreamRuns.Load())
	}
}

func TestIDEUserinfoRunSpendCheckLimited429(t *testing.T) {
	const ideKey = "pkide_test_cap"
	const limitedMsg = "【小脉】会员额度已用尽"
	var capHits atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
	})
	var upstreamRuns atomic.Int32
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		upstreamRuns.Add(1)
		w.WriteHeader(http.StatusOK)
	})
	fuSrv := httptest.NewTLSServer(mux)
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			var body struct {
				PulseKey string `json:"pulse_key"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.PulseKey != ideKey {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-ide-1", "mode": "quota", "scope": "ide",
			})
		case "/api/internal/v1/proxy/spend-check":
			capHits.Add(1)
			var body struct {
				ProxyKeyID string `json:"proxy_key_id"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.ProxyKeyID != "pk-ide-1" {
				t.Errorf("spend-check proxy_key_id=%q", body.ProxyKeyID)
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "limited", "message": limitedMsg,
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), NewSessionMap())
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
	s.transport = &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, s)
	t.Cleanup(func() { ln.Close() })
	caPEM, _ := os.ReadFile(caPath)

	client := connectClientAuth(t, ln.Addr().String(), caPEM, ideKey, "x")
	upstreamAddr := strings.TrimPrefix(fuSrv.URL, "https://")
	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+testJWT("ide-user"))
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(runResp.Body)
	runResp.Body.Close()
	if runResp.StatusCode != http.StatusTooManyRequests {
		t.Fatalf("expected 429, got %d body %s", runResp.StatusCode, body)
	}
	if !strings.Contains(string(body), limitedMsg) {
		t.Fatalf("body %s", body)
	}
	if capHits.Load() != 1 {
		t.Fatalf("spend checks %d", capHits.Load())
	}
	if upstreamRuns.Load() != 0 {
		t.Fatalf("upstream must not be called")
	}
}

func TestLoanPassthroughSkipsSpendCheck(t *testing.T) {
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
		if r.URL.Path == "/api/internal/v1/proxy/spend-check" {
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
		t.Fatalf("loan_passthrough must not call spend-check, hits=%d", capHits.Load())
	}
}

func TestNonRunBillingSkipsSpendCheck(t *testing.T) {
	const pkKey = "pk_nonrun_test"
	var capHits atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer keyA" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-nonrun",
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/aiserver.v1.AiService/StreamChat", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/internal/v1/proxy/spend-check" {
			capHits.Add(1)
		}
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			var body struct {
				PulseKey string `json:"pulse_key"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.PulseKey != pkKey {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-nonrun", "mode": "quota",
			})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), NewSessionMap())
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
	exReq.Header.Set("Authorization", "Bearer "+pkKey)
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)
	exResp.Body.Close()

	chatReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/aiserver.v1.AiService/StreamChat", bytes.NewReader([]byte{0}))
	chatReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	chatResp, err := client.Do(chatReq)
	if err != nil {
		t.Fatal(err)
	}
	chatResp.Body.Close()
	if capHits.Load() != 0 {
		t.Fatalf("non-Run billing must not call spend-check, hits=%d", capHits.Load())
	}
}

func TestSpendCheckHTTP500Returns503(t *testing.T) {
	const pkKey = "pk_503_test"
	var upstreamRuns atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-503",
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		upstreamRuns.Add(1)
		w.WriteHeader(http.StatusOK)
	})
	fuSrv := httptest.NewTLSServer(mux)
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-503", "mode": "quota",
			})
		case "/api/internal/v1/proxy/spend-check":
			w.WriteHeader(http.StatusInternalServerError)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), NewSessionMap())
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
	s.transport = &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
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
	exReq.Header.Set("Authorization", "Bearer "+pkKey)
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)
	exResp.Body.Close()

	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(runResp.Body)
	runResp.Body.Close()
	if runResp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d body %s", runResp.StatusCode, body)
	}
	if !strings.Contains(string(body), spendCheckUnavailableMsg) {
		t.Fatalf("body %s", body)
	}
	if upstreamRuns.Load() != 0 {
		t.Fatalf("upstream must not be called")
	}
}

func TestSpendCheckTimeoutReturns503(t *testing.T) {
	const pkKey = "pk_timeout_test"
	var upstreamRuns atomic.Int32

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-timeout",
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		upstreamRuns.Add(1)
		w.WriteHeader(http.StatusOK)
	})
	fuSrv := httptest.NewTLSServer(mux)
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-timeout", "mode": "quota",
			})
		case "/api/internal/v1/proxy/spend-check":
			time.Sleep(200 * time.Millisecond)
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok"})
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pulseClient := NewPulseClient(pulse.URL, "tok", time.Minute)
	pulseClient.client = &http.Client{
		Timeout:   50 * time.Millisecond,
		Transport: http.DefaultTransport,
	}

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, pulseClient, NewSessionMap())
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
	s.transport = &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
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
	exReq.Header.Set("Authorization", "Bearer "+pkKey)
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)
	exResp.Body.Close()

	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(runResp.Body)
	runResp.Body.Close()
	if runResp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d body %s", runResp.StatusCode, body)
	}
	if !strings.Contains(string(body), spendCheckUnavailableMsg) {
		t.Fatalf("body %s", body)
	}
	if upstreamRuns.Load() != 0 {
		t.Fatalf("upstream must not be called")
	}
}

func TestUsageItemClientCLI(t *testing.T) {
	const pkKey = "pk_client_cli"
	var usageBodies [][]byte

	inner := append(append(varintField(1, 10), varintField(2, 5)...), varintField(5, 0)...)
	turnEnded := msgField(1, msgField(14, inner))

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-cli-usage",
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
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-cli", "mode": "quota",
			})
		case "/api/internal/v1/proxy/spend-check":
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok"})
		case "/api/internal/v1/proxy/usage":
			b, _ := io.ReadAll(r.Body)
			usageBodies = append(usageBodies, b)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pulseClient := NewPulseClient(pulse.URL, "tok", time.Minute)
	pulseClient.usageBatchMax = 1
	s := NewServer(pool, ca, pulseClient, NewSessionMap())
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
	exReq.Header.Set("Authorization", "Bearer "+pkKey)
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)
	exResp.Body.Close()

	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+exOut.AccessToken)
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, runResp.Body)
	runResp.Body.Close()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && len(usageBodies) < 1 {
		time.Sleep(10 * time.Millisecond)
	}
	if len(usageBodies) < 1 {
		t.Fatal("expected usage POST")
	}
	var payload struct {
		Items []UsageItem `json:"items"`
	}
	if err := json.Unmarshal(usageBodies[0], &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Items) != 1 || payload.Items[0].Client != "cli" {
		t.Fatalf("items=%+v", payload.Items)
	}
}

func TestUsageItemClientIDE(t *testing.T) {
	const ideKey = "pkide_client_test"
	var usageBodies [][]byte

	inner := append(append(varintField(1, 7), varintField(2, 3)...), varintField(5, 0)...)
	turnEnded := msgField(1, msgField(14, inner))

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken": "tokA", "refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/Run", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/connect+proto")
		_ = writeEnvelope(w, 0x00, turnEnded)
		_ = writeEnvelope(w, endStreamFlag, []byte(`{"metadata":{}}`))
	})
	fuSrv := httptest.NewTLSServer(mux)
	t.Cleanup(fuSrv.Close)

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			var body struct {
				PulseKey string `json:"pulse_key"`
			}
			_ = json.NewDecoder(r.Body).Decode(&body)
			if body.PulseKey != ideKey {
				_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pk-ide-usage", "mode": "quota", "scope": "ide",
			})
		case "/api/internal/v1/proxy/spend-check":
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok"})
		case "/api/internal/v1/proxy/usage":
			b, _ := io.ReadAll(r.Body)
			usageBodies = append(usageBodies, b)
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()
	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pulseClient := NewPulseClient(pulse.URL, "tok", time.Minute)
	pulseClient.usageBatchMax = 1
	s := NewServer(pool, ca, pulseClient, NewSessionMap())
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
	s.transport = &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, s)
	t.Cleanup(func() { ln.Close() })
	caPEM, _ := os.ReadFile(caPath)
	client := connectClientAuth(t, ln.Addr().String(), caPEM, ideKey, "x")
	upstreamAddr := strings.TrimPrefix(fuSrv.URL, "https://")

	runReq, _ := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+"/agent.v1.AgentService/Run", bytes.NewReader([]byte{0}))
	runReq.Header.Set("Authorization", "Bearer "+testJWT("ide-usage-user"))
	runReq.Header.Set("Content-Type", "application/connect+proto")
	runResp, err := client.Do(runReq)
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, runResp.Body)
	runResp.Body.Close()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) && len(usageBodies) < 1 {
		time.Sleep(10 * time.Millisecond)
	}
	if len(usageBodies) < 1 {
		t.Fatal("expected usage POST")
	}
	var payload struct {
		Items []UsageItem `json:"items"`
	}
	if err := json.Unmarshal(usageBodies[0], &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Items) != 1 || payload.Items[0].Client != "ide" {
		t.Fatalf("items=%+v", payload.Items)
	}
}
