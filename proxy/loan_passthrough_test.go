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
	"testing"
	"time"
)

func TestLoanPassthroughExchangeEmptyPool(t *testing.T) {
	const loanKey = "crsr_test_loan_key_abc"
	var upstreamAuth string

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		upstreamAuth = r.Header.Get("Authorization")
		if upstreamAuth != "Bearer "+loanKey {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-loan",
			"refreshToken": "r",
		})
	})
	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)
	fu := &fakeUpstream{Server: fuSrv}

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		var body struct {
			PulseKey string `json:"pulse_key"`
		}
		_ = json.NewDecoder(r.Body).Decode(&body)
		if body.PulseKey != loanKey {
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid"})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status":         "ok",
			"mode":           "loan_passthrough",
			"proxy_key_id":   nil,
			"loan_id":        "loan-1",
			"credential_id":  "cred-1",
			"reason":         nil,
		})
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool(nil)
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

	req, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+loanKey)
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
		AccessToken string `json:"accessToken"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if out.AccessToken != "jwt-loan" {
		t.Fatalf("accessToken=%q want jwt-loan", out.AccessToken)
	}
	if upstreamAuth != "Bearer "+loanKey {
		t.Fatalf("upstream Authorization=%q want Bearer %s", upstreamAuth, loanKey)
	}
	b, ok := sessions.Lookup("jwt-loan")
	if !ok {
		t.Fatal("session not bound")
	}
	if b.Mode != "loan_passthrough" || b.LoanID != "loan-1" || b.CredentialID != "cred-1" || b.PulseKey != loanKey {
		t.Fatalf("session binding: %+v", b)
	}
	if b.ProxyKeyID != "" {
		t.Fatalf("ProxyKeyID should be empty, got %q", b.ProxyKeyID)
	}
}

func TestLoanPassthroughMITMUsage(t *testing.T) {
	const loanKey = "crsr_test_loan_key_abc"
	var upstreamAuth string

	inner := append(append(varintField(1, 1234), varintField(2, 56)...), varintField(5, 7)...)
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
		upstreamAuth = r.Header.Get("Authorization")
		if upstreamAuth != "Bearer jwt-loan" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Header().Set("Content-Type", "application/connect+proto")
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

	var (
		usageBodies [][]byte
	)
	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
				"proxy_key_id":  nil,
				"loan_id":       "loan-1",
				"credential_id": "cred-1",
			})
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

	exReq, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	exReq.Header.Set("Authorization", "Bearer "+loanKey)
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
	if exOut.AccessToken != "jwt-loan" {
		t.Fatalf("accessToken=%q want jwt-loan", exOut.AccessToken)
	}

	modelProto := msgField(1, []byte("claude-4-sonnet"))
	runBody := make([]byte, 5+len(modelProto))
	runBody[0] = 0
	binary.BigEndian.PutUint32(runBody[1:5], uint32(len(modelProto)))
	copy(runBody[5:], modelProto)
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

	if upstreamAuth != "Bearer jwt-loan" {
		t.Fatalf("upstream Authorization=%q want Bearer jwt-loan", upstreamAuth)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if len(usageBodies) >= 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
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
	if item.LoanID != "loan-1" {
		t.Fatalf("loan_id=%q", item.LoanID)
	}
	if item.CredentialID != "cred-1" {
		t.Fatalf("credential_id=%q", item.CredentialID)
	}
	if item.ProxyKeyID != "" {
		t.Fatalf("proxy_key_id=%q want empty", item.ProxyKeyID)
	}
	if item.Tokens.Input != 1234 || item.Tokens.Output != 56 || item.Tokens.Reasoning != 7 {
		t.Fatalf("tokens=%+v", item.Tokens)
	}
}

func TestLoanPassthroughMITMAuthFailReportsLoan(t *testing.T) {
	const loanKey = "crsr_test_loan_key_abc"
	var eventBodies [][]byte

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
		w.WriteHeader(http.StatusUnauthorized)
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
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status":        "ok",
				"mode":          "loan_passthrough",
				"proxy_key_id":  nil,
				"loan_id":       "loan-1",
				"credential_id": "cred-1",
			})
		case "/api/internal/v1/proxy/events":
			b, _ := io.ReadAll(r.Body)
			eventBodies = append(eventBodies, b)
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool([]string{"pool-key-should-stay"})
	pool.exchangeBase = fuSrv.URL
	pool.client = fuSrv.Client()

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
	upstreamAddr := strings.TrimPrefix(fuSrv.URL, "https://")

	exReq, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	exReq.Header.Set("Authorization", "Bearer "+loanKey)
	exReq.Header.Set("Content-Type", "application/json")
	exResp, err := client.Do(exReq)
	if err != nil {
		t.Fatal(err)
	}
	defer exResp.Body.Close()
	var exOut struct {
		AccessToken string `json:"accessToken"`
	}
	_ = json.NewDecoder(exResp.Body).Decode(&exOut)

	runReq, err := http.NewRequest(
		http.MethodPost,
		"https://"+upstreamAddr+"/agent.v1.AgentService/Run",
		bytes.NewReader([]byte{0x0A}),
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
	if runResp.StatusCode != http.StatusUnauthorized {
		b, _ := io.ReadAll(runResp.Body)
		t.Fatalf("run status %d body %s", runResp.StatusCode, b)
	}

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if len(eventBodies) >= 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	if len(eventBodies) < 1 {
		t.Fatal("expected Pulse event POST")
	}
	var evPayload struct {
		Events []EventItem `json:"events"`
	}
	if err := json.Unmarshal(eventBodies[0], &evPayload); err != nil {
		t.Fatal(err)
	}
	if len(evPayload.Events) != 1 {
		t.Fatalf("events=%+v", evPayload.Events)
	}
	ev := evPayload.Events[0]
	if ev.LoanID != "loan-1" {
		t.Fatalf("loan_id=%q", ev.LoanID)
	}
	if ev.ProxyKeyID != "" {
		t.Fatalf("proxy_key_id=%q want empty", ev.ProxyKeyID)
	}
	if pool.keys[0].exhausted {
		t.Fatal("pool key should not be marked on loan passthrough auth fail")
	}
}

func TestLoanAliasExchangeUsesServerCursorKey(t *testing.T) {
	const aliasKey = "pka_test_alias_key_abc"
	const cursorKey = "crsr_bound_cursor_key_xyz"
	var upstreamAuth string

	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		upstreamAuth = r.Header.Get("Authorization")
		if upstreamAuth != "Bearer "+cursorKey {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "jwt-alias",
			"refreshToken": "r",
		})
	})
	fuSrv := httptest.NewUnstartedServer(mux)
	fuSrv.EnableHTTP2 = true
	fuSrv.StartTLS()
	t.Cleanup(fuSrv.Close)
	fu := &fakeUpstream{Server: fuSrv}

	pulse := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
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
			"proxy_key_id":   nil,
			"loan_id":        "loan-alias-1",
			"credential_id":  "cred-alias-1",
			"cursor_api_key": cursorKey,
			"reason":         nil,
		})
	}))
	t.Cleanup(pulse.Close)

	pool := NewPool(nil)
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

	req, err := http.NewRequest(http.MethodPost, "https://"+upstreamAddr+exchangePath, bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+aliasKey)
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
		AccessToken string `json:"accessToken"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	if out.AccessToken != "jwt-alias" {
		t.Fatalf("accessToken=%q want jwt-alias", out.AccessToken)
	}
	if upstreamAuth != "Bearer "+cursorKey {
		t.Fatalf("upstream Authorization=%q want Bearer %s", upstreamAuth, cursorKey)
	}
	b, ok := sessions.Lookup("jwt-alias")
	if !ok {
		t.Fatal("session not bound")
	}
	if b.Mode != "loan_alias" || b.LoanID != "loan-alias-1" || b.CredentialID != "cred-alias-1" {
		t.Fatalf("session binding: %+v", b)
	}
	if b.PulseKey != aliasKey {
		t.Fatalf("PulseKey=%q want %s", b.PulseKey, aliasKey)
	}
	if b.CursorAPIKey != cursorKey {
		t.Fatalf("CursorAPIKey=%q want %s", b.CursorAPIKey, cursorKey)
	}
	if b.ProxyKeyID != "" {
		t.Fatalf("ProxyKeyID should be empty, got %q", b.ProxyKeyID)
	}
}
