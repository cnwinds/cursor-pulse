package main

import (
	"bytes"
	"crypto/tls"
	"encoding/base64"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"context"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// testServers lets sub-lock tests flip flags on a running harness server.
var (
	srvMu       sync.Mutex
	testServers = map[string]*Server{}
)

// newFakeUpstreamIDE emulates api2.cursor.sh for IDE tests: the token exchange
// endpoint plus two IDE-probe endpoints that echo the Authorization they
// received, so tests can assert rewrite vs passthrough behavior.
func newFakeUpstreamIDE(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if key != "keyA" && key != "keyB" && key != "crsr_loan_key" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "tok" + key[len(key)-1:],
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/aiserver.v1.TestService/Unary", func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "upstream-auth="+r.Header.Get("Authorization"))
	})
	mux.HandleFunc("/aiserver.v1.DashboardService/GetMe", func(w http.ResponseWriter, r *http.Request) {
		io.WriteString(w, "upstream-auth="+r.Header.Get("Authorization"))
	})
	srv := httptest.NewTLSServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

// newFakePulseCounted is newFakePulse with an authorize counter, so tests can
// assert that an IDE binding is reused instead of re-authorizing per request.
func newFakePulseCounted(t *testing.T) (*httptest.Server, *atomic.Int32) {
	t.Helper()
	var calls atomic.Int32
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
		if body.PulseKey != "pk_ide" {
			_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid", "proxy_key_id": ""})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "proxy_key_id": "pkIDE1", "mode": "quota", "reason": nil,
		})
	}))
	t.Cleanup(srv.Close)
	return srv, &calls
}

// newIDETestProxy wires a Pulse-mode proxy against the given upstream and fake
// Pulse, mirroring newPulseTestProxy but with an IDE key and TLS test upstream.
func newIDETestProxy(t *testing.T, upstreamURL string, pulseURL string, ideKey string) (addr string, caPEM []byte, sessions *SessionMap) {
	t.Helper()
	pool := NewPool([]string{"keyA", "keyB"})
	pool.exchangeBase = upstreamURL
	pool.client = &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}

	ca, caPath, _, err := loadOrCreateCA(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	pulse := NewPulseClient(pulseURL, "tok", time.Minute)
	pulse.usageBatchMax = 1 // flush on every EnqueueUsage (test determinism)
	sessions = NewSessionMap()
	s := NewServer(pool, ca, pulse, sessions)
	s.idePulseKey = ideKey
	s.caPEMPath = caPath
	reg := newIDEPortRegistry(s, 9300, filepath.Join(t.TempDir(), "ide_ports.json"))
	s.idePorts = reg
	reg.load()
	t.Cleanup(reg.Close)
	s.shouldMITM = func(string) bool { return true }
	permitTestConnect(s)
	s.transport = &http.Transport{
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true},
	}

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	go http.Serve(ln, s)
	srvMu.Lock()
	testServers[ln.Addr().String()] = s
	srvMu.Unlock()
	t.Cleanup(func() {
		ln.Close()
		srvMu.Lock()
		delete(testServers, ln.Addr().String())
		srvMu.Unlock()
	})

	pemBytes, err := os.ReadFile(caPath)
	if err != nil {
		t.Fatal(err)
	}
	return ln.Addr().String(), pemBytes, sessions
}

func ideUpstreamHost(t *testing.T, upstreamURL string) string {
	t.Helper()
	host := strings.TrimPrefix(upstreamURL, "https://")
	host = strings.TrimPrefix(host, "http://")
	return host
}

func TestIDEIdentityPassthrough(t *testing.T) {
	cases := map[string]bool{
		"/aiserver.v1.DashboardService/GetMe":                  true,
		"/aiserver.v1.DashboardService/GetUserProfile":         true,
		"/aiserver.v1.DashboardService/GetTeams":               true,
		"/aiserver.v1.DashboardService/GetTeamCommands":        true,
		"/aiserver.v1.AiService/GetUserStatus":                 true,
		"/aiserver.v1.DashboardService/GetFilteredUsageEvents": false,
		"/aiserver.v1.AiService/StreamChat":                    false,
		"/agent.v1.AgentService/RunSSE":                        false,
		"/auth/full_user":                                      false,
	}
	for path, want := range cases {
		if got := ideIdentityPassthrough(path); got != want {
			t.Fatalf("ideIdentityPassthrough(%q) = %v want %v", path, got, want)
		}
	}
}

func TestIDEBindsUnboundSessionToIDEKey(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, authCalls := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	client := connectClient(t, proxyAddr, caPEM)
	host := ideUpstreamHost(t, fu.URL)

	doUnary := func() (int, string) {
		req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
			bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer ide-login-jwt")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		b, _ := io.ReadAll(resp.Body)
		return resp.StatusCode, string(b)
	}

	status, body := doUnary()
	if status != http.StatusOK {
		t.Fatalf("first business request: status %d body %s", status, body)
	}
	if !strings.Contains(body, "upstream-auth=Bearer tokA") && !strings.Contains(body, "upstream-auth=Bearer tokB") {
		t.Fatalf("Authorization not rewritten to pool token: %s", body)
	}
	// First request: TOFU bind plus (since the seat architecture) one seat
	// advisor consult from sticky.Select — both hit /authorize.
	afterFirst := authCalls.Load()
	if afterFirst < 1 || afterFirst > 2 {
		t.Fatalf("expected 1-2 authorizes for TOFU bind (+seat advisor), got %d", afterFirst)
	}

	// Second request reuses the binding: no new authorize, still rewritten.
	status, body = doUnary()
	if status != http.StatusOK || !strings.Contains(body, "upstream-auth=Bearer tok") {
		t.Fatalf("second business request: status %d body %s", status, body)
	}
	if authCalls.Load() != afterFirst {
		t.Fatalf("binding should be reused without re-authorize, got %d -> %d calls", afterFirst, authCalls.Load())
	}
}

func TestIDEIdentityRPCPassesThroughClientToken(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, authCalls := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	client := connectClient(t, proxyAddr, caPEM)
	host := ideUpstreamHost(t, fu.URL)

	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.DashboardService/GetMe",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer ide-login-jwt")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GetMe status %d body %s", resp.StatusCode, b)
	}
	if !strings.Contains(string(b), "upstream-auth=Bearer ide-login-jwt") {
		t.Fatalf("identity RPC must forward the client's own token, got: %s", b)
	}
	if authCalls.Load() != 0 {
		t.Fatalf("identity RPC must not touch Pulse, got %d authorize calls", authCalls.Load())
	}
}

// After a business request TOFU-binds the login JWT, GetMe must still forward
// the client's token — not the pool credential selected for that session.
func TestIDEIdentityRPCPassthroughAfterTOFUBind(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	client := connectClient(t, proxyAddr, caPEM)
	host := ideUpstreamHost(t, fu.URL)

	bindReq, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	bindReq.Header.Set("Authorization", "Bearer ide-login-jwt")
	bindResp, err := client.Do(bindReq)
	if err != nil {
		t.Fatal(err)
	}
	bindBody, _ := io.ReadAll(bindResp.Body)
	bindResp.Body.Close()
	if bindResp.StatusCode != http.StatusOK {
		t.Fatalf("TOFU bind: status %d body %s", bindResp.StatusCode, bindBody)
	}
	if !strings.Contains(string(bindBody), "upstream-auth=Bearer tok") {
		t.Fatalf("business request should rewrite to pool token, got: %s", bindBody)
	}

	meReq, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.DashboardService/GetMe",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	meReq.Header.Set("Authorization", "Bearer ide-login-jwt")
	meResp, err := client.Do(meReq)
	if err != nil {
		t.Fatal(err)
	}
	defer meResp.Body.Close()
	meBody, _ := io.ReadAll(meResp.Body)
	if meResp.StatusCode != http.StatusOK {
		t.Fatalf("GetMe after bind: status %d body %s", meResp.StatusCode, meBody)
	}
	if !strings.Contains(string(meBody), "upstream-auth=Bearer ide-login-jwt") {
		t.Fatalf("GetMe after TOFU bind must still passthrough login JWT, got: %s", meBody)
	}
	if strings.Contains(string(meBody), "upstream-auth=Bearer tok") {
		t.Fatalf("GetMe must not be rewritten to pool credential after bind: %s", meBody)
	}
}

func TestIDEWithoutKeyStillRequiresExchange(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "")
	client := connectClient(t, proxyAddr, caPEM)
	host := ideUpstreamHost(t, fu.URL)

	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer ide-login-jwt")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		b, _ := io.ReadAll(resp.Body)
		t.Fatalf("legacy behavior is 401 for unbound tokens, got %d body %s", resp.StatusCode, b)
	}
}

func TestIDESuspendedKeyRejected(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "suspended", "proxy_key_id": "pk1", "mode": "quota",
			"reason": "account suspended",
		})
	}))
	t.Cleanup(srv.Close)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, srv.URL, "pk_ide")
	client := connectClient(t, proxyAddr, caPEM)
	host := ideUpstreamHost(t, fu.URL)

	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer ide-login-jwt")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusForbidden || !strings.Contains(string(b), "suspended") {
		t.Fatalf("suspended ide key: status %d body %s", resp.StatusCode, b)
	}
}

func TestIDEPerKeyPortAttribution(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, sessions := newIDETestProxy(t, fu.URL, pulse.URL, "")
	host := ideUpstreamHost(t, fu.URL)

	// Allocate a per-key IDE port via the bootstrap endpoint.
	resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=pk_ide")
	if err != nil {
		t.Fatal(err)
	}
	var portResp struct {
		Port      string `json:"port"`
		ProxyHost string `json:"proxy_host"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&portResp); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || portResp.Port == "" {
		t.Fatalf("/ide-port: status %d resp %v", resp.StatusCode, portResp)
	}

	// Same key returns the same port (idempotent allocation).
	resp2, err := http.Get("http://" + proxyAddr + "/ide-port?key=pk_ide")
	if err != nil {
		t.Fatal(err)
	}
	var portResp2 struct {
		Port string `json:"port"`
	}
	_ = json.NewDecoder(resp2.Body).Decode(&portResp2)
	resp2.Body.Close()
	if portResp2.Port != portResp.Port {
		t.Fatalf("port changed on re-alloc: %s -> %s", portResp.Port, portResp2.Port)
	}

	// Invalid key is rejected.
	resp3, err := http.Get("http://" + proxyAddr + "/ide-port?key=pk_unknown")
	if err != nil {
		t.Fatal(err)
	}
	resp3.Body.Close()
	if resp3.StatusCode != http.StatusUnauthorized {
		t.Fatalf("unknown key: status %d want 401", resp3.StatusCode)
	}

	// A business request through the dedicated port binds to the key with no
	// server-wide -ide-pulse-key configured. (Authorize results are cached by
	// PulseClient, so call counting cannot observe the bind — assert the
	// resulting session binding instead.)
	client := connectClient(t, net.JoinHostPort("127.0.0.1", portResp.Port), caPEM)
	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer ide-login-jwt")
	r, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	if r.StatusCode != http.StatusOK || !strings.Contains(string(b), "upstream-auth=Bearer tok") {
		t.Fatalf("per-key port request: status %d body %s", r.StatusCode, b)
	}
	binding, ok := sessions.Lookup("ide-login-jwt")
	if !ok || binding.ProxyKeyID != "pkIDE1" {
		t.Fatalf("session should bind to the per-key identity, got ok=%v binding=%+v", ok, binding)
	}
}

// newFakeUpstreamSSE emulates agent.v1.AgentService/RunSSE exactly as Cursor's
// backend does: Content-Type text/event-stream, body of plain Connect
// envelopes, TurnEndedUpdate nested in InteractionUpdate field 14.
func newFakeUpstreamSSE(t *testing.T) *httptest.Server {
	t.Helper()
	mux := http.NewServeMux()
	mux.HandleFunc(exchangePath, func(w http.ResponseWriter, r *http.Request) {
		key := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
		if key != "keyA" && key != "keyB" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{
			"accessToken":  "tok" + key[len(key)-1:],
			"refreshToken": "r",
		})
	})
	mux.HandleFunc("/agent.v1.AgentService/RunSSE", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		turnEnded := msgField(14, append(append(varintField(1, 3595), varintField(2, 998)...), varintField(3, 151103)...))
		interaction := msgField(1, turnEnded)
		writeEnvelope(w, 0x00, interaction)
		if f, ok := w.(http.Flusher); ok {
			f.Flush()
		}
		writeEnvelope(w, endStreamFlag, []byte(`{"metadata":{}}`))
	})
	srv := httptest.NewTLSServer(mux)
	t.Cleanup(srv.Close)
	return srv
}

func TestIDERunSSEUsageTappedThroughPerKeyPort(t *testing.T) {
	fu := newFakeUpstreamSSE(t)

	var mu sync.Mutex
	var usageBodies []map[string]any
	pulseSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/api/internal/v1/proxy/authorize":
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": "pkIDE1", "mode": "quota", "reason": nil,
			})
		case "/api/internal/v1/proxy/usage":
			var body map[string]any
			_ = json.NewDecoder(r.Body).Decode(&body)
			mu.Lock()
			usageBodies = append(usageBodies, body)
			mu.Unlock()
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"recorded":1,"suspended":[]}`))
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(pulseSrv.Close)

	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulseSrv.URL, "")
	portResp, err := http.Get("http://" + proxyAddr + "/ide-port?key=pk_ide")
	if err != nil {
		t.Fatal(err)
	}
	var pr struct {
		Port string `json:"port"`
	}
	_ = json.NewDecoder(portResp.Body).Decode(&pr)
	portResp.Body.Close()

	client := connectClient(t, net.JoinHostPort("127.0.0.1", pr.Port), caPEM)
	req, err := http.NewRequest(http.MethodPost, "https://"+ideUpstreamHost(t, fu.URL)+"/agent.v1.AgentService/RunSSE",
		bytes.NewReader([]byte{}))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer ide-login-jwt")
	req.Header.Set("Content-Type", "application/connect+proto")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("RunSSE status %d body %s", resp.StatusCode, b)
	}

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := len(usageBodies)
		mu.Unlock()
		if n > 0 {
			break
		}
		time.Sleep(50 * time.Millisecond)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(usageBodies) == 0 {
		t.Fatal("SSE-labeled Connect stream produced no usage report")
	}
	items, _ := usageBodies[0]["items"].([]any)
	if len(items) == 0 {
		t.Fatalf("usage envelope has no items: %v", usageBodies[0])
	}
	item, _ := items[0].(map[string]any)
	if item["proxy_key_id"] != "pkIDE1" {
		t.Fatalf("usage attributed to %v want pkIDE1", item["proxy_key_id"])
	}
	toks, _ := item["tokens"].(map[string]any)
	if toks == nil || toks["input"] != float64(3595) || toks["output"] != float64(998) {
		t.Fatalf("token counts mismatch: %v", item)
	}
}

// testJWT builds a minimal JWS-shaped token whose payload carries the sub.
func testJWT(sub string) string {
	header := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"RS256","typ":"JWT"}`))
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"` + sub + `"}`))
	return header + "." + payload + ".sig"
}

func TestIDELockSubRejectsForeignLogin(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	host := ideUpstreamHost(t, fu.URL)

	// Enable the lock on the test server (mirrors PROXY_IDE_LOCK_SUB).
	s := serverByAddr(t, proxyAddr)
	s.ideLockSubInit(true)

	client := connectClient(t, proxyAddr, caPEM)
	doUnary := func(token string) int {
		req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
			bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		io.Copy(io.Discard, resp.Body)
		return resp.StatusCode
	}

	if code := doUnary(testJWT("user-a")); code != http.StatusOK {
		t.Fatalf("first login should bind: %d", code)
	}
	// Same identity, new token: allowed.
	if code := doUnary(testJWT("user-a")); code != http.StatusOK {
		t.Fatalf("same sub should pass: %d", code)
	}
	// Different identity on the same IDE key: rejected.
	if code := doUnary(testJWT("user-b")); code != http.StatusForbidden {
		t.Fatalf("foreign sub must be rejected, got %d", code)
	}
}

func TestIDELockSubOffAllowsAnyLogin(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	host := ideUpstreamHost(t, fu.URL)
	client := connectClient(t, proxyAddr, caPEM)

	for _, sub := range []string{"user-a", "user-b"} {
		req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
			bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+testJWT(sub))
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("lock off: sub %s got %d", sub, resp.StatusCode)
		}
	}
}

func TestJwtSubParsing(t *testing.T) {
	if got := jwtSub(testJWT("github|user_123")); got != "github|user_123" {
		t.Fatalf("jwtSub: %q", got)
	}
	if got := jwtSub("not-a-jwt"); got != "" {
		t.Fatalf("non-JWT must yield empty, got %q", got)
	}
	if got := jwtSub("a.!!!.c"); got != "" {
		t.Fatalf("undecodable payload must yield empty, got %q", got)
	}
	noneHdr := base64.RawURLEncoding.EncodeToString([]byte(`{"alg":"none","typ":"JWT"}`))
	payload := base64.RawURLEncoding.EncodeToString([]byte(`{"sub":"attacker"}`))
	if got := jwtSub(noneHdr + "." + payload + ".sig"); got != "" {
		t.Fatalf("alg=none must yield empty, got %q", got)
	}
}

func TestSafeProxyHostRejectsInjection(t *testing.T) {
	if !isSafeProxyHost("127.0.0.1:8317") {
		t.Fatal("loopback:port must be accepted")
	}
	if !isSafeProxyHost("proxy.example.com") {
		t.Fatal("hostname must be accepted")
	}
	for _, bad := range []string{
		"evil.com\r\nWrite-Host pwned",
		"evil.com'; calc.exe #",
		"evil.com'$(calc)",
		"host with spaces",
		"",
	} {
		if isSafeProxyHost(bad) {
			t.Fatalf("must reject %q", bad)
		}
	}
	req := httptest.NewRequest(http.MethodGet, "http://x/setup-cursor.ps1", nil)
	req.Host = "evil.com\r\nWrite-Host pwned"
	rr := httptest.NewRecorder()
	(&Server{}).serveSetupScript(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("injected Host must 400, got %d body %q", rr.Code, rr.Body.String())
	}
	if strings.Contains(rr.Body.String(), "Write-Host") {
		t.Fatal("injected Host must not appear in response body")
	}
}

// serverByAddr reaches into the running test proxy to flip test-only flags.
func serverByAddr(t *testing.T, addr string) *Server {
	t.Helper()
	srvMu.Lock()
	defer srvMu.Unlock()
	if s, ok := testServers[addr]; ok {
		return s
	}
	t.Fatalf("no test server for %s", addr)
	return nil
}

func TestIDELockSubEnforcedOnPerKeyPort(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "")
	host := ideUpstreamHost(t, fu.URL)

	// Enable the lock BEFORE the per-key listener is cloned so the clone
	// shares the store (regression: the clone used to miss the lock fields
	// entirely, silently disabling PROXY_IDE_LOCK_SUB on IDE ports).
	s := serverByAddr(t, proxyAddr)
	s.ideLockSubInit(true)

	resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=pk_ide")
	if err != nil {
		t.Fatal(err)
	}
	var pr struct {
		Port string `json:"port"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&pr)
	resp.Body.Close()
	if pr.Port == "" {
		t.Fatal("no port allocated")
	}
	client := connectClient(t, net.JoinHostPort("127.0.0.1", pr.Port), caPEM)
	doUnary := func(sub string) int {
		req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
			bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+testJWT(sub))
		r, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer r.Body.Close()
		io.Copy(io.Discard, r.Body)
		return r.StatusCode
	}
	if code := doUnary("user-a"); code != http.StatusOK {
		t.Fatalf("first login should bind via per-key port: %d", code)
	}
	if code := doUnary("user-b"); code != http.StatusForbidden {
		t.Fatalf("foreign sub must be rejected on per-key port, got %d", code)
	}
}

func TestIDEEmptyAuthorizationRejected(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	host := ideUpstreamHost(t, fu.URL)
	client := connectClient(t, proxyAddr, caPEM)

	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	// No Authorization header at all: must never be TOFU-bound to the IDE key.
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("empty Authorization: got %d want 401", resp.StatusCode)
	}
}

// newFakePulseLoan serves a loan_alias authorize result with a candidate
// allowlist, mirroring Auto-Assigned Loan keys used as IDE Proxy Keys.
func newFakePulseLoan(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "proxy_key_id": "", "loan_id": "loan-1",
			"mode": "loan_alias", "reason": nil,
			"credential_id": "local-0", "cursor_api_key": "crsr_loan_key",
			"credential_ids": []string{"local-0", "local-1"},
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestIDELoanAliasBindingFields(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse := newFakePulseLoan(t)
	proxyAddr, caPEM, sessions := newIDETestProxy(t, fu.URL, pulse.URL, "")
	host := ideUpstreamHost(t, fu.URL)

	resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=pka_loan")
	if err != nil {
		t.Fatal(err)
	}
	var pr struct {
		Port string `json:"port"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&pr)
	resp.Body.Close()

	client := connectClient(t, net.JoinHostPort("127.0.0.1", pr.Port), caPEM)
	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+testJWT("user-a"))
	r, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	if r.StatusCode != http.StatusOK {
		t.Fatalf("loan business request: %d %s", r.StatusCode, b)
	}
	binding, ok := sessions.Lookup(testJWT("user-a"))
	if !ok {
		t.Fatal("session not bound")
	}
	if binding.Mode != "loan_alias" || binding.LoanID != "loan-1" ||
		binding.CursorAPIKey != "crsr_loan_key" ||
		len(binding.AllowedCredentialIDs) != 2 {
		t.Fatalf("loan binding fields wrong: %+v", binding)
	}
}

// newFakePulseLoanPool serves a loan_pool authorize result (routing_mode=pool
// pka_ key roaming across pool candidates).
func newFakePulseLoanPool(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/internal/v1/proxy/authorize" {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"status": "ok", "proxy_key_id": "", "loan_id": "loan-pool-1",
			"mode": "loan_pool", "reason": nil,
			"credential_id": "local-0",
		})
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestIDELoanPoolBindingServesViaSticky(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse := newFakePulseLoanPool(t)
	proxyAddr, caPEM, sessions := newIDETestProxy(t, fu.URL, pulse.URL, "")
	host := ideUpstreamHost(t, fu.URL)

	// loan_pool keys must pass the /ide-port mode gate (regression: they used
	// to 500 "authorize misconfigured" because the mode postdates the IDE path).
	resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=pka_pool")
	if err != nil {
		t.Fatal(err)
	}
	var pr struct {
		Port string `json:"port"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&pr)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || pr.Port == "" {
		t.Fatalf("/ide-port for loan_pool: status %d port %q", resp.StatusCode, pr.Port)
	}

	client := connectClient(t, net.JoinHostPort("127.0.0.1", pr.Port), caPEM)
	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+testJWT("user-a"))
	r, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer r.Body.Close()
	b, _ := io.ReadAll(r.Body)
	if r.StatusCode != http.StatusOK || !strings.Contains(string(b), "upstream-auth=Bearer tok") {
		t.Fatalf("loan_pool business request: %d %s", r.StatusCode, b)
	}
	binding, ok := sessions.Lookup(testJWT("user-a"))
	if !ok || binding.Mode != "loan_pool" || binding.LoanID != "loan-pool-1" {
		t.Fatalf("loan_pool binding wrong: ok=%v %+v", ok, binding)
	}
}

func TestIDETTLReauthorizeUsesSeatFlow(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, authCalls := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	host := ideUpstreamHost(t, fu.URL)
	client := connectClient(t, proxyAddr, caPEM)

	doUnary := func() int {
		req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
			bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+testJWT("user-a"))
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		io.Copy(io.Discard, resp.Body)
		return resp.StatusCode
	}
	if code := doUnary(); code != http.StatusOK {
		t.Fatalf("first request: %d", code)
	}

	// Expire the binding: the next request must run the seat-aware TTL
	// re-authorize (AuthorizeSeat via the shared /authorize endpoint) and
	// still serve with a pool credential.
	s := serverByAddr(t, proxyAddr)
	s.sessionTTL = 1 * time.Nanosecond
	before := authCalls.Load()
	if code := doUnary(); code != http.StatusOK {
		t.Fatalf("request after TTL expiry (seat reauth): %d", code)
	}
	if authCalls.Load() <= before {
		t.Fatalf("TTL expiry should re-authorize via the seat flow, calls %d -> %d", before, authCalls.Load())
	}
}

func TestIDEPortReleaseLifecycle(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, _, _ := newIDETestProxy(t, fu.URL, pulse.URL, "")

	getPort := func(key string) (int, int) {
		resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=" + key)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var pr struct {
			Port string `json:"port"`
		}
		if resp.StatusCode != http.StatusOK {
			return 0, resp.StatusCode
		}
		_ = json.NewDecoder(resp.Body).Decode(&pr)
		n, _ := strconv.Atoi(pr.Port)
		return n, resp.StatusCode
	}

	port1, code := getPort("pk_ide")
	if code != http.StatusOK || port1 == 0 {
		t.Fatalf("allocate: port=%d code=%d", port1, code)
	}
	// DELETE with the wrong key leaves the listener alone.
	req, _ := http.NewRequest(http.MethodDelete, "http://"+proxyAddr+"/ide-port?key=pk_other", nil)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("release unknown key: %d want 404", resp.StatusCode)
	}
	if p, _ := getPort("pk_ide"); p != port1 {
		t.Fatalf("allocation changed after foreign release: %d -> %d", port1, p)
	}

	// DELETE with the right key releases: 204, port closes, next allocate
	// may reuse the same port, persistence shrinks.
	req, _ = http.NewRequest(http.MethodDelete, "http://"+proxyAddr+"/ide-port?key=pk_ide", nil)
	resp, err = http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("release: %d want 204", resp.StatusCode)
	}
	// The released listener is closed: dialing it must fail quickly.
	dialCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	conn, err := (&net.Dialer{}).DialContext(dialCtx, "tcp", net.JoinHostPort("127.0.0.1", strconv.Itoa(port1)))
	if err == nil {
		conn.Close()
		t.Fatalf("listener on :%d should be closed after release", port1)
	}
	// Re-allocating the same key yields a working port again.
	port2, code := getPort("pk_ide")
	if code != http.StatusOK || port2 == 0 {
		t.Fatalf("re-allocate: port=%d code=%d", port2, code)
	}
}

func TestUninstallScriptEndpoint(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, _, _ := newIDETestProxy(t, fu.URL, pulse.URL, "")

	resp, err := http.Get("http://" + proxyAddr + "/uninstall-cursor.ps1")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	script := string(b)
	if resp.StatusCode != http.StatusOK ||
		!strings.Contains(script, "http://"+proxyAddr) ||
		!strings.Contains(script, "PSObject.Properties.Remove($name)") ||
		!strings.Contains(script, "'http.proxy', 'cursor.general.disableHttp2', 'http.systemCertificates'") ||
		!strings.Contains(script, "delstore Root") {
		t.Fatalf("uninstall script malformed: status %d head %q", resp.StatusCode, script[:min(200, len(script))])
	}
}

func TestSetupBootstrapEndpoints(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")

	resp, err := http.Get("http://" + proxyAddr + "/ca.pem")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK || !bytes.Equal(b, caPEM) {
		t.Fatalf("/ca.pem: status %d len %d (want %d)", resp.StatusCode, len(b), len(caPEM))
	}

	resp2, err := http.Get("http://" + proxyAddr + "/setup-cursor.ps1")
	if err != nil {
		t.Fatal(err)
	}
	defer resp2.Body.Close()
	s, _ := io.ReadAll(resp2.Body)
	script := string(s)
	if resp2.StatusCode != http.StatusOK ||
		!strings.Contains(script, "http://"+proxyAddr) ||
		!strings.Contains(script, "cursor.general.disableHttp2") ||
		!strings.Contains(script, "http.proxy") {
		t.Fatalf("/setup-cursor.ps1: status %d script %q", resp2.StatusCode, script[:min(200, len(script))])
	}

	resp3, err := http.Get("http://" + proxyAddr + "/health")
	if err != nil {
		t.Fatal(err)
	}
	resp3.Body.Close()
	if resp3.StatusCode != http.StatusOK {
		t.Fatalf("/health status %d", resp3.StatusCode)
	}
}

func TestAllowlistCoversCursorComBlindTunnel(t *testing.T) {
	patterns := parseConnectAllowlist("")
	for _, host := range []string{"api2.cursor.sh:443", "marketplace.cursorapi.com:443", "www.cursor.com:443", "cursor.com"} {
		if !hostAllowed(host, patterns) {
			t.Fatalf("default allowlist should allow %s", host)
		}
	}
	if hostAllowed("evil.com:443", patterns) {
		t.Fatal("default allowlist must not allow unrelated hosts")
	}
}

// newFakePulseKeys serves authorize ok for a fixed map of pulse key → proxy_key_id.
func newFakePulseKeys(t *testing.T, keys map[string]string) *httptest.Server {
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
		if id, ok := keys[body.PulseKey]; ok {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"status": "ok", "proxy_key_id": id, "mode": "quota", "reason": nil,
			})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "invalid", "proxy_key_id": ""})
	}))
	t.Cleanup(srv.Close)
	return srv
}

func TestIDECrossPortSessionRebindsPulseKey(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse := newFakePulseKeys(t, map[string]string{
		"pk_a": "pkA",
		"pk_b": "pkB",
	})
	proxyAddr, caPEM, sessions := newIDETestProxy(t, fu.URL, pulse.URL, "")
	host := ideUpstreamHost(t, fu.URL)

	alloc := func(key string) string {
		t.Helper()
		resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=" + key)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var pr struct {
			Port string `json:"port"`
		}
		_ = json.NewDecoder(resp.Body).Decode(&pr)
		if resp.StatusCode != http.StatusOK || pr.Port == "" {
			t.Fatalf("alloc %s: status %d port %q", key, resp.StatusCode, pr.Port)
		}
		return pr.Port
	}
	portA, portB := alloc("pk_a"), alloc("pk_b")
	tok := "same-login-jwt"

	doUnary := func(port string) int {
		t.Helper()
		client := connectClient(t, net.JoinHostPort("127.0.0.1", port), caPEM)
		req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
			bytes.NewReader([]byte("{}")))
		if err != nil {
			t.Fatal(err)
		}
		req.Header.Set("Authorization", "Bearer "+tok)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		io.Copy(io.Discard, resp.Body)
		return resp.StatusCode
	}

	if code := doUnary(portA); code != http.StatusOK {
		t.Fatalf("bind on A: %d", code)
	}
	b, ok := sessions.Lookup(tok)
	if !ok || b.PulseKey != "pk_a" || b.ProxyKeyID != "pkA" {
		t.Fatalf("after A: ok=%v binding=%+v", ok, b)
	}
	if code := doUnary(portB); code != http.StatusOK {
		t.Fatalf("rebind on B: %d", code)
	}
	b, ok = sessions.Lookup(tok)
	if !ok || b.PulseKey != "pk_b" || b.ProxyKeyID != "pkB" {
		t.Fatalf("after B must attribute to pk_b, got ok=%v binding=%+v", ok, b)
	}
}

func TestIDEDedicatedPortHidesIdePortEndpoint(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, _, _ := newIDETestProxy(t, fu.URL, pulse.URL, "")

	resp, err := http.Get("http://" + proxyAddr + "/ide-port?key=pk_ide")
	if err != nil {
		t.Fatal(err)
	}
	var pr struct {
		Port string `json:"port"`
	}
	_ = json.NewDecoder(resp.Body).Decode(&pr)
	resp.Body.Close()
	if pr.Port == "" {
		t.Fatal("expected dedicated port")
	}

	// Hitting /ide-port on the dedicated listener itself must 404.
	resp2, err := http.Get("http://127.0.0.1:" + pr.Port + "/ide-port?key=pk_ide")
	if err != nil {
		t.Fatal(err)
	}
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusNotFound {
		t.Fatalf("dedicated /ide-port: got %d want 404", resp2.StatusCode)
	}
}

func TestIDELockSubRejectsMissingSub(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, caPEM, _ := newIDETestProxy(t, fu.URL, pulse.URL, "pk_ide")
	host := ideUpstreamHost(t, fu.URL)
	s := serverByAddr(t, proxyAddr)
	s.ideLockSubInit(true)

	client := connectClient(t, proxyAddr, caPEM)
	req, err := http.NewRequest(http.MethodPost, "https://"+host+"/aiserver.v1.TestService/Unary",
		bytes.NewReader([]byte("{}")))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer not-a-jwt")
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	io.Copy(io.Discard, resp.Body)
	if resp.StatusCode != http.StatusForbidden {
		t.Fatalf("lock on + no sub: got %d want 403", resp.StatusCode)
	}
}

func TestIDEPortMapConflictReallocates(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse := newFakePulseKeys(t, map[string]string{"pk_a": "pkA", "pk_b": "pkB"})
	dir := t.TempDir()
	path := filepath.Join(dir, "ide_ports.json")
	// Corrupt map: two keys claim the same port.
	if err := os.WriteFile(path, []byte(`{"pk_a":9401,"pk_b":9401}`), 0o600); err != nil {
		t.Fatal(err)
	}

	pool := NewPool([]string{"keyA"})
	pool.exchangeBase = fu.URL
	pool.client = &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}}
	ca, _, _, err := loadOrCreateCA(dir)
	if err != nil {
		t.Fatal(err)
	}
	s := NewServer(pool, ca, NewPulseClient(pulse.URL, "tok", time.Minute), NewSessionMap())
	reg := newIDEPortRegistry(s, 9401, path)
	reg.setListenHost("127.0.0.1:8317")
	s.idePorts = reg
	reg.load()
	t.Cleanup(reg.Close)

	reg.mu.Lock()
	portA, okA := reg.byKey["pk_a"]
	portB, okB := reg.byKey["pk_b"]
	reg.mu.Unlock()
	if !okA || !okB {
		t.Fatalf("both keys should be restored/reallocated: a=%v b=%v", okA, okB)
	}
	if portA == portB {
		t.Fatalf("conflict must yield distinct ports, both %d", portA)
	}
	if reg.listenHost != "127.0.0.1" {
		t.Fatalf("listenHost=%q want 127.0.0.1", reg.listenHost)
	}
}

func TestSetupScriptAllocatesPortBeforeWritingSettings(t *testing.T) {
	fu := newFakeUpstreamIDE(t)
	pulse, _ := newFakePulseCounted(t)
	proxyAddr, _, _ := newIDETestProxy(t, fu.URL, pulse.URL, "")
	resp, err := http.Get("http://" + proxyAddr + "/setup-cursor.ps1")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	script := string(b)
	idePortAt := strings.Index(script, "/ide-port?key=")
	writeAt := strings.Index(script, "WriteAllText($settingsPath")
	if idePortAt < 0 || writeAt < 0 || idePortAt > writeAt {
		t.Fatalf("setup must call /ide-port before writing settings (idePort=%d write=%d)", idePortAt, writeAt)
	}
}

var _ = tls.VersionTLS12 // keep import if helpers change
