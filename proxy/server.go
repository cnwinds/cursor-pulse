package main

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

type Server struct {
	pool       *Pool
	ca         *CA
	pulse      *PulseClient
	sessions   *SessionMap
	sticky     *StickySelect
	sessionTTL time.Duration
	// sessionTokens is nil when PROXY_OPAQUE_SESSION_TOKEN is off; exchange
	// then hands clients the upstream Cursor JWT.
	sessionTokens *sessionTokenMinter
	onRotate      func(entry *keyEntry, binding SessionBinding, kind failKind)
	transport     *http.Transport

	passthroughMu sync.Mutex
	passthrough   map[string]*keyEntry // credentialID → cached loan key JWT

	// cpSticky remembers Coding-Plan (pkcp_) gateway sticky affinity per
	// pulse key. Pointer-held so the main port and every per-key IDE listener
	// share one view.
	cpSticky *cpStickyState

	// shouldMITM reports whether a CONNECT target's TLS should be intercepted
	// (true for Cursor backends); other allowlisted hosts are tunneled blindly.
	shouldMITM func(authority string) bool

	connectAllowlist []string

	// idePulseKey is the proxy key (pk_/pka_) that IDE-originated sessions on
	// this listener bind to. Empty on the main port without -ide-pulse-key
	// keeps the legacy behavior: business requests must present an
	// exchange-issued session JWT (CLI-only proxy).
	idePulseKey string

	// ideSub is the shared login-identity lock state (PROXY_IDE_LOCK_SUB).
	// Held by pointer so the main port and every per-key listener enforce one
	// consistent view. Nil means the lock is off.
	ideSub *ideSubStore

	// caPEMPath is the on-disk MITM root CA, served at GET /ca.pem so member
	// machines can bootstrap trust from the proxy address alone.
	caPEMPath string

	// idePorts allocates one dedicated listen port per Pulse access key so IDE
	// sessions attribute to a member without client-side key support. Nil on
	// non-Pulse deployments.
	idePorts *idePortRegistry
}

// cpStickyState remembers Coding-Plan (pkcp_) gateway sticky affinity:
// which credential each pkcp_ pulse key used last. Nil-safe methods.
type cpStickyState struct {
	mu   sync.Mutex
	cred map[string]string // pkcp_ pulse key → last credential id
}

func (c *cpStickyState) get(pulseKey string) string {
	if c == nil {
		return ""
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.cred[pulseKey]
}

func (c *cpStickyState) set(pulseKey, credentialID string) {
	if c == nil || pulseKey == "" || credentialID == "" {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.cred == nil {
		c.cred = map[string]string{}
	}
	c.cred[pulseKey] = credentialID
}

func NewServer(pool *Pool, ca *CA, pulse *PulseClient, sessions *SessionMap) *Server {
	s := &Server{
		pool:             pool,
		ca:               ca,
		pulse:            pulse,
		sessions:         sessions,
		sticky:           NewStickySelect(pool, sessions),
		sessionTokens:    newSessionTokenMinter(),
		transport:        newOutboundTransport(nil),
		shouldMITM:       defaultShouldMITM,
		connectAllowlist: resolveConnectAllowlist(),
		cpSticky:         &cpStickyState{},
	}
	s.useSeatAdvisor()
	return s
}

func (s *Server) useSeatAdvisor() {
	if s == nil || s.sticky == nil || s.pulse == nil {
		return
	}
	s.sticky.SetAdvisor(func(binding *SessionBinding, current string, release bool, pool quotaPoolKind) (string, []string, bool, error) {
		if binding == nil || strings.TrimSpace(binding.PulseKey) == "" {
			return "", nil, false, nil
		}
		res, err := s.pulse.AuthorizeSeat(binding.PulseKey, current, release, pool, binding.heldOutside(pool))
		if err != nil {
			return "", nil, false, err
		}
		return res.AssignedCredentialID, res.BlockedCredentialIDs, res.SeatAdvised, nil
	})
}

// SetUpstreamProxy routes MITM upstream (Cursor) traffic via the given proxy.
func (s *Server) SetUpstreamProxy(upstream *url.URL) {
	s.transport = newOutboundTransport(upstream)
}

func defaultShouldMITM(authority string) bool {
	host := authority
	if h, _, err := net.SplitHostPort(authority); err == nil {
		host = h
	}
	host = strings.ToLower(host)
	return host == "cursor.sh" || strings.HasSuffix(host, ".cursor.sh")
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if isOpenAICompatPath(r) {
		s.handleOpenAICompat(w, r)
		return
	}
	if r.Method == http.MethodConnect {
		s.handleConnect(w, r)
		return
	}
	// Plain-HTTP bootstrap endpoints so a member machine can onboard from the
	// proxy address alone (no file distribution): trust the CA, then run the
	// Cursor IDE setup script. Everything else stays CONNECT-only.
	if r.URL.Path == "/ide-port" && (r.Method == http.MethodGet || r.Method == http.MethodDelete) {
		s.serveIDEPort(w, r)
		return
	}
	if r.Method == http.MethodGet {
		switch r.URL.Path {
		case "/ca.pem":
			s.serveCAPEM(w)
			return
		case "/setup-cursor.ps1":
			serveScript(w, r, cursorIDESetupScriptTemplate)
			return
		case "/uninstall-cursor.ps1":
			serveScript(w, r, cursorIDEUninstallScriptTemplate)
			return
		case "/", "/health":
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			fmt.Fprintf(w, "cursor-quota-proxy\n\nGET /ca.pem            - MITM root CA (install into trusted roots)\nGET /setup-cursor.ps1 - one-line Cursor IDE onboarding (PowerShell)\nGET /uninstall-cursor.ps1 - one-line Cursor IDE offboarding\nGET /ide-port?key=... - dedicated IDE port for a Proxy Key (DELETE releases)\n")
			return
		}
	}
	http.Error(w, "cursor-quota-proxy: CONNECT only (or use /openai/v1 for Coding Plan)", http.StatusBadRequest)
}

func (s *Server) handleConnect(w http.ResponseWriter, r *http.Request) {
	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "hijacking unsupported", http.StatusInternalServerError)
		return
	}
	client, _, err := hj.Hijack()
	if err != nil {
		return
	}
	authority := r.Host

	if !hostAllowed(authority, s.connectAllowlist) {
		writeHTTPError(client, http.StatusForbidden)
		client.Close()
		return
	}

	if !s.shouldMITM(authority) {
		// Blind tunnel for non-Cursor traffic.
		upstream, err := net.DialTimeout("tcp", authority, 15*time.Second)
		if err != nil {
			writeHTTPError(client, http.StatusBadGateway)
			client.Close()
			return
		}
		if _, err := client.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n")); err != nil {
			upstream.Close()
			client.Close()
			return
		}
		go tunnel(upstream, client)
		return
	}

	host := authority
	if h, _, err := net.SplitHostPort(authority); err == nil {
		host = h
	}
	leaf, err := s.ca.certFor(host)
	if err != nil {
		log.Printf("[mitm] cert for %s: %v", host, err)
		writeHTTPError(client, http.StatusInternalServerError)
		client.Close()
		return
	}
	tlsConf := &tls.Config{
		Certificates: []tls.Certificate{leaf},
		NextProtos:   []string{"h2", "http/1.1"},
		MinVersion:   tls.VersionTLS12,
	}
	if _, err := client.Write([]byte("HTTP/1.1 200 Connection Established\r\n\r\n")); err != nil {
		client.Close()
		return
	}
	tlsConn := tls.Server(client, tlsConf)

	// Serve this single connection as an HTTP server (h2 via ALPN, or h1).
	srv := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
			// Carry the per-key listener's proxy key across the CONNECT hop.
			if k := ideKeyFromCtx(r.Context()); k != "" {
				req = req.WithContext(withIDEKeyCtx(req.Context(), k))
			}
			s.handleMITM(w, req, authority)
		}),
		TLSConfig:         tlsConf,
		ReadHeaderTimeout: defaultReadHeaderTimeout,
		IdleTimeout:       defaultIdleTimeout,
	}
	go srv.Serve(&oneConnListener{conn: tlsConn})
}

func tunnel(dst, src net.Conn) {
	done := make(chan struct{}, 2)
	go func() { io.Copy(dst, src); done <- struct{}{} }()
	go func() { io.Copy(src, dst); done <- struct{}{} }()
	<-done
	dst.Close()
	src.Close()
}

func writeHTTPError(c net.Conn, status int) {
	text := http.StatusText(status)
	fmt.Fprintf(c, "HTTP/1.1 %d %s\r\nContent-Length: 0\r\n\r\n", status, text)
}

// oneConnListener adapts a single net.Conn to net.Listener for http.Server.
type oneConnListener struct {
	conn net.Conn
	done bool
}

func (l *oneConnListener) Accept() (net.Conn, error) {
	if l.done {
		return nil, io.EOF
	}
	l.done = true
	return l.conn, nil
}

func (l *oneConnListener) Close() error   { return nil }
func (l *oneConnListener) Addr() net.Addr { return l.conn.LocalAddr() }

// passthroughToken returns a JWT for a loan-bound session (passthrough or alias),
// caching by credential ID and re-exchanging the Cursor API key when needed.
// For loan_alias, re-authorizes with Pulse so revoked loans cannot keep exchanging
// and reassignment picks up the new underlying Cursor key immediately.
func (s *Server) passthroughToken(ctx context.Context, binding SessionBinding) (*keyEntry, string, error) {
	apiKey := binding.PulseKey
	credID := binding.CredentialID
	if binding.Mode == "loan_alias" {
		if s.pulse == nil || strings.TrimSpace(binding.PulseKey) == "" {
			return nil, "", fmt.Errorf("loan_alias re-authorize unavailable")
		}
		res, err := s.pulse.AuthorizeSeat(binding.PulseKey, binding.CredentialID, false, quotaPoolAuto, nil)
		if err != nil {
			return nil, "", err
		}
		if res.Status != "ok" || res.Mode != "loan_alias" {
			return nil, "", fmt.Errorf("loan_alias unauthorized: status=%s mode=%s", res.Status, res.Mode)
		}
		apiKey = strings.TrimSpace(res.CursorAPIKey)
		if apiKey == "" {
			return nil, "", fmt.Errorf("loan_alias missing cursor_api_key")
		}
		if strings.TrimSpace(res.CredentialID) != "" {
			credID = res.CredentialID
		}
	}

	s.passthroughMu.Lock()
	if s.passthrough == nil {
		s.passthrough = map[string]*keyEntry{}
	}
	entry, ok := s.passthrough[credID]
	if !ok {
		entry = &keyEntry{
			credentialID: credID,
			apiKey:       apiKey,
		}
		s.passthrough[credID] = entry
	} else if apiKey != "" {
		entry.apiKey = apiKey
	}
	s.passthroughMu.Unlock()

	exchCtx, cancel := context.WithTimeout(context.Background(), exchangeTimeout)
	defer cancel()
	tok, err := entry.ensureToken(exchCtx, s.pool.client, s.pool.exchangeBase)
	if err != nil {
		return entry, "", err
	}
	return entry, tok, nil
}
