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
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// newFakeUpstreamIDE emulates api2.cursor.sh for IDE tests: the token exchange
// endpoint plus two IDE-probe endpoints that echo the Authorization they
// received, so tests can assert rewrite vs passthrough behavior.
func newFakeUpstreamIDE(t *testing.T) *httptest.Server {
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
	t.Cleanup(func() { ln.Close() })

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
	if authCalls.Load() != 1 {
		t.Fatalf("expected 1 authorize for the TOFU bind, got %d", authCalls.Load())
	}

	// Second request reuses the binding: no new authorize, still rewritten.
	status, body = doUnary()
	if status != http.StatusOK || !strings.Contains(body, "upstream-auth=Bearer tok") {
		t.Fatalf("second business request: status %d body %s", status, body)
	}
	if authCalls.Load() != 1 {
		t.Fatalf("binding should be reused without re-authorize, got %d calls", authCalls.Load())
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

var _ = tls.VersionTLS12 // keep import if helpers change
