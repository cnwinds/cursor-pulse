package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
)

// Per-key IDE listeners (deprecated): each Proxy Key gets its own dedicated
// proxy port. New clients use the shared main port with Proxy-Authorization
// userinfo (pkide_). Dedicated listeners remain for transition compatibility.

const defaultIDEPortBase = 9100

type tunnelKeySource string

const (
	tunnelKeySourceListener tunnelKeySource = "listener"
	tunnelKeySourceUserinfo tunnelKeySource = "userinfo"
)

type tunnelKeyCtx struct {
	key    string
	source tunnelKeySource
}

type tunnelKeyCtxKey struct{}

func withTunnelKey(ctx context.Context, key string, source tunnelKeySource) context.Context {
	return context.WithValue(ctx, tunnelKeyCtxKey{}, tunnelKeyCtx{key: key, source: source})
}

func tunnelKeyFromCtx(ctx context.Context) (string, tunnelKeySource) {
	v, ok := ctx.Value(tunnelKeyCtxKey{}).(tunnelKeyCtx)
	if !ok {
		return "", ""
	}
	return v.key, v.source
}

func withIDEKeyCtx(ctx context.Context, key string) context.Context {
	return withTunnelKey(ctx, key, tunnelKeySourceListener)
}

// ideKeyFromCtx returns the per-key listener's proxy key; empty on the main
// listener or when the key came from Proxy-Authorization userinfo.
func ideKeyFromCtx(ctx context.Context) string {
	k, src := tunnelKeyFromCtx(ctx)
	if src == tunnelKeySourceListener {
		return k
	}
	return ""
}

// withIDEKeyHandler scopes a proxy key to every request served by a per-key
// IDE listener. Context injection instead of a cloned Server: one instance,
// so every field, lock, cache, and future addition stays shared by
// construction — no per-field copy list to maintain.
func withIDEKeyHandler(h http.Handler, key string) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h.ServeHTTP(w, r.WithContext(withIDEKeyCtx(r.Context(), key)))
	})
}

type idePortRegistry struct {
	mu         sync.Mutex
	base       int
	listenHost string         // host part of PROXY_LISTEN; empty / 0.0.0.0 → all interfaces
	byKey      map[string]int // proxy key → allocated port
	byPort     map[int]string // port → proxy key (conflict guard)
	listeners  map[int]net.Listener
	path       string // persistence file; empty disables persistence
	parent     *Server
}

func newIDEPortRegistry(parent *Server, base int, path string) *idePortRegistry {
	if base <= 0 {
		base = defaultIDEPortBase
	}
	return &idePortRegistry{
		base:      base,
		byKey:     map[string]int{},
		byPort:    map[int]string{},
		listeners: map[int]net.Listener{},
		path:      path,
		parent:    parent,
	}
}

// setListenHost pins per-key listeners to the same host as PROXY_LISTEN so a
// main port bound to 127.0.0.1 does not silently expose IDE ports on 0.0.0.0.
func (r *idePortRegistry) setListenHost(listenAddr string) {
	host, _, err := net.SplitHostPort(listenAddr)
	if err != nil {
		log.Printf("[ide] PROXY_LISTEN=%q is not host:port — per-key ports fall back to all interfaces: %v",
			listenAddr, err)
		return
	}
	host = strings.TrimSpace(host)
	if host == "" || host == "0.0.0.0" || host == "::" {
		r.listenHost = ""
		return
	}
	r.listenHost = host
}

func (r *idePortRegistry) load() {
	if r.path == "" {
		return
	}
	b, err := os.ReadFile(r.path)
	if err != nil {
		return
	}
	var m map[string]int
	if err := json.Unmarshal(b, &m); err != nil {
		log.Printf("[ide] port map %s unreadable: %v", r.path, err)
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	dirty := false
	for key, port := range m {
		if port <= 0 || strings.TrimSpace(key) == "" {
			continue
		}
		if owner, taken := r.byPort[port]; taken && owner != key {
			log.Printf("[ide] port %d conflict (owned by %s, wanted %s) — reallocating",
				port, maskClientToken(owner), maskClientToken(key))
			if _, err := r.allocateLocked(key); err != nil {
				log.Printf("[ide] reallocate for %s: %v", maskClientToken(key), err)
			} else {
				dirty = true
			}
			continue
		}
		if _, err := r.listenLocked(key, port); err != nil {
			log.Printf("[ide] reopen listener on :%d: %v — reallocating", port, err)
			if _, err := r.allocateLocked(key); err != nil {
				log.Printf("[ide] reallocate for %s: %v", maskClientToken(key), err)
			} else {
				dirty = true
			}
			continue
		}
		r.byKey[key] = port
	}
	if dirty {
		r.saveLocked()
	}
	if len(r.byKey) > 0 {
		log.Printf("[ide] restored %d per-key listener(s)", len(r.byKey))
	}
}

func (r *idePortRegistry) saveLocked() {
	if r.path == "" {
		return
	}
	b, err := json.MarshalIndent(r.byKey, "", "  ")
	if err != nil {
		return
	}
	if err := os.MkdirAll(filepath.Dir(r.path), 0o700); err != nil {
		log.Printf("[ide] save port map: %v", err)
		return
	}
	// The plaintext proxy keys are persisted (0600, same trust level as the
	// local -keys config) because restored listeners must be able to
	// re-authorize against Pulse after a restart.
	if err := os.WriteFile(r.path, b, 0o600); err != nil {
		log.Printf("[ide] save port map: %v", err)
	}
}

// listenLocked opens (or reuses) the listener for a port. Caller holds mu.
// The listener serves the parent server with the key scoped per request —
// see withIDEKeyHandler. Refuses to reuse a port already bound to a different
// key.
func (r *idePortRegistry) listenLocked(key string, port int) (net.Listener, error) {
	if owner, ok := r.byPort[port]; ok && owner != key {
		return nil, fmt.Errorf("port %d already bound to another key", port)
	}
	if ln, ok := r.listeners[port]; ok {
		r.byPort[port] = key
		return ln, nil
	}
	addr := fmt.Sprintf(":%d", port)
	if r.listenHost != "" {
		addr = net.JoinHostPort(r.listenHost, strconv.Itoa(port))
	}
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, err
	}
	r.listeners[port] = ln
	r.byPort[port] = key
	// Match the main port's Slowloris baseline (ReadHeaderTimeout / IdleTimeout).
	srv := &http.Server{
		Handler:           withIDEKeyHandler(r.parent, key),
		ReadHeaderTimeout: defaultReadHeaderTimeout,
		IdleTimeout:       defaultIdleTimeout,
	}
	go srv.Serve(ln)
	return ln, nil
}

// allocateLocked picks a free port for key, listens, and records byKey.
// Caller holds mu. Does not persist — callers save when needed.
func (r *idePortRegistry) allocateLocked(key string) (int, error) {
	if port, ok := r.byKey[key]; ok {
		return port, nil
	}
	used := map[int]bool{}
	for _, p := range r.byKey {
		used[p] = true
	}
	for p := range r.byPort {
		used[p] = true
	}
	port := 0
	for p := r.base; p < r.base+1000; p++ {
		if !used[p] {
			port = p
			break
		}
	}
	if port == 0 {
		return 0, fmt.Errorf("no free ide ports")
	}
	if n := len(r.byKey); n >= 900 {
		log.Printf("[ide] warning: %d/1000 ide ports in use before allocating :%d", n, port)
	}
	if _, err := r.listenLocked(key, port); err != nil {
		return 0, err
	}
	r.byKey[key] = port
	return port, nil
}

// portForKey validates the proxy key (when Pulse is configured) and returns the
// key's dedicated port, allocating one on first use.
func (r *idePortRegistry) portForKey(w http.ResponseWriter, key string) (int, bool) {
	key = strings.TrimSpace(key)
	if key == "" {
		http.Error(w, "missing key", http.StatusBadRequest)
		return 0, false
	}
	if r.parent.pulse != nil {
		// Fresh authorize so a revoked key cannot keep allocating ports from
		// a stale ok cache entry (default auth TTL is 60s).
		res, ok := authorizeOrRejectFresh(w, r.parent.pulse, key)
		if !ok {
			return 0, false
		}
		if res.ProxyKeyID == "" && res.Mode != "loan_passthrough" && res.Mode != "loan_alias" && res.Mode != "loan_pool" {
			http.Error(w, "authorize misconfigured", http.StatusInternalServerError)
			return 0, false
		}
	}

	r.mu.Lock()
	defer r.mu.Unlock()
	if port, ok := r.byKey[key]; ok {
		return port, true
	}
	port, err := r.allocateLocked(key)
	if err != nil {
		log.Printf("[ide] allocate for %s: %v", maskClientToken(key), err)
		http.Error(w, "listen failed: "+err.Error(), http.StatusServiceUnavailable)
		return 0, false
	}
	r.saveLocked()
	log.Printf("[ide] allocated port %d for proxy key %s", port, maskClientToken(key))
	return port, true
}

// release closes the listener and forgets the key's port allocation (DELETE
// /ide-port — the uninstall path). No Pulse validation on purpose: uninstall
// must also work for keys that were revoked after allocation. Returns the
// released port, or 0 when the key had none.
func (r *idePortRegistry) release(key string) int {
	key = strings.TrimSpace(key)
	if key == "" {
		return 0
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	port, ok := r.byKey[key]
	if !ok {
		return 0
	}
	if ln, ok := r.listeners[port]; ok {
		ln.Close()
		delete(r.listeners, port)
	}
	delete(r.byKey, key)
	delete(r.byPort, port)
	r.saveLocked()
	log.Printf("[ide] released port %d for proxy key %s", port, maskClientToken(key))
	return port
}

// Close releases every per-key listener (used by tests; production listeners
// live for the process lifetime).
func (r *idePortRegistry) Close() {
	r.mu.Lock()
	defer r.mu.Unlock()
	for port, ln := range r.listeners {
		ln.Close()
		delete(r.listeners, port)
	}
	r.byPort = map[int]string{}
}

// serveIDEPort answers GET /ide-port?key=... with {"port": N, "proxy_host": H}
// and DELETE /ide-port?key=... (uninstall) with 204 once the listener closes.
// Both are main-listener bootstrap concerns: a dedicated port must not mint or
// drop listeners for other keys.
func (s *Server) serveIDEPort(w http.ResponseWriter, r *http.Request) {
	log.Printf("[ide] deprecated: /ide-port is deprecated; re-run setup-cursor.ps1 to use the shared port with an IDE key (pkide_)")
	if ideKeyFromCtx(r.Context()) != "" {
		http.Error(w, "ide-port only on main listener", http.StatusNotFound)
		return
	}
	if s.idePorts == nil {
		http.Error(w, "ide ports disabled (requires Pulse mode)", http.StatusServiceUnavailable)
		return
	}
	if r.Method == http.MethodDelete {
		key := r.URL.Query().Get("key")
		if strings.TrimSpace(key) == "" {
			http.Error(w, "missing key", http.StatusBadRequest)
			return
		}
		if port := s.idePorts.release(key); port > 0 {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		http.Error(w, "no port allocated for key", http.StatusNotFound)
		return
	}
	port, ok := s.idePorts.portForKey(w, r.URL.Query().Get("key"))
	if !ok {
		return
	}
	host := safeEchoHost(r, s.idePorts.listenHost)
	if h, _, err := net.SplitHostPort(host); err == nil && h != "" {
		host = h
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{
		"port":       strconv.Itoa(port),
		"proxy_host": host,
	})
}

// safeEchoHost returns a Host safe to embed in setup scripts / JSON. Malicious
// or malformed Host headers (CRLF, quotes, spaces) fall back to listenHost or
// 127.0.0.1 — never echo unvalidated input into PowerShell.
func safeEchoHost(r *http.Request, listenHost string) string {
	host := strings.TrimSpace(r.Host)
	if host != "" && isSafeProxyHost(host) {
		return host
	}
	if listenHost != "" && isSafeProxyHost(listenHost) {
		return listenHost
	}
	return "127.0.0.1"
}

// isSafeProxyHost allows hostname[:port], IPv4, or [IPv6]:port only — no
// whitespace, quotes, or control characters that could break script embedding.
func isSafeProxyHost(host string) bool {
	if host == "" || len(host) > 253 {
		return false
	}
	for _, r := range host {
		if r < 0x20 || r == 0x7f || r == '\'' || r == '"' || r == '`' || r == ';' || r == '$' || r == '|' {
			return false
		}
	}
	h, port, err := net.SplitHostPort(host)
	if err != nil {
		h = host
		port = ""
	}
	if port != "" {
		n, err := strconv.Atoi(port)
		if err != nil || n < 1 || n > 65535 {
			return false
		}
	}
	if h == "" {
		return false
	}
	ipCand := h
	if strings.HasPrefix(ipCand, "[") && strings.HasSuffix(ipCand, "]") {
		ipCand = ipCand[1 : len(ipCand)-1]
	}
	if ip := net.ParseIP(ipCand); ip != nil {
		return true
	}
	for i, r := range h {
		ok := (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '.' || r == '-'
		if !ok {
			return false
		}
		if (r == '-' || r == '.') && (i == 0 || i == len(h)-1) {
			return false
		}
	}
	return true
}
